# Copyright (c) 2026 Slavcho Spasenoski. Original Bi-Dash contributions.
# SPDX-License-Identifier: BSD-2-Clause
"""Bi-Dash roles cap legacy group permissions; groups still control data access."""

ROLES = ("admin", "editor", "reader")
DEFAULT_POLICY = {
    "editor": {"query_timeout_seconds": 60, "max_concurrent_queries": 2,
               "allow_schedule": False, "allow_public_sharing": False,
               "allow_delete": False, "allow_ai": True},
    "reader": {"allow_download": True},
}
EDITOR_PERMISSIONS = {
    "create_dashboard", "create_query", "edit_dashboard", "edit_query", "view_query", "view_source",
    "execute_query", "list_dashboards", "list_alerts", "list_data_sources", "schedule_query",
    "view_results", "download_results", "use_ai", "share_dashboard", "delete_assets",
}
READER_PERMISSIONS = {"list_dashboards", "view_results", "download_results"}


def policy_for(org):
    stored = org.settings.get("access_policy", {})
    return {role: {**defaults, **stored.get(role, {})} for role, defaults in DEFAULT_POLICY.items()}


def validate_policy(body):
    if not isinstance(body, dict) or set(body) != set(DEFAULT_POLICY):
        raise ValueError("Provide Editor and Reader policies.")
    result = {}
    for role, defaults in DEFAULT_POLICY.items():
        values = body[role]
        if not isinstance(values, dict) or set(values) != set(defaults):
            raise ValueError(f"Invalid {role} policy fields.")
        result[role] = {}
        for field, default in defaults.items():
            value = values[field]
            if isinstance(default, bool):
                if not isinstance(value, bool):
                    raise ValueError(f"{field} must be true or false.")
            elif type(value) is not int or not 1 <= value <= (3600 if field == "query_timeout_seconds" else 20):
                raise ValueError(f"{field} is outside the allowed range.")
            result[role][field] = value
    return result


def validate_limits(body):
    if not isinstance(body, dict) or set(body) - {"query_timeout_seconds", "max_concurrent_queries"}:
        raise ValueError("Invalid user query limits.")
    result = {}
    for field, value in body.items():
        if value is None:
            continue
        if type(value) is not int or not 1 <= value <= (3600 if field == "query_timeout_seconds" else 20):
            raise ValueError(f"Invalid {field}.")
        result[field] = value
    return result


def effective_permissions(role, group_permissions, policy):
    if role == "admin":
        return sorted(EDITOR_PERMISSIONS | READER_PERMISSIONS | set(group_permissions) |
                      {"admin", "super_admin", "list_users", "schedule_query"})
    if role == "reader":
        permissions = set(READER_PERMISSIONS)
        if not policy["reader"]["allow_download"]:
            permissions.discard("download_results")
        return sorted(permissions)
    permissions = set(group_permissions) & EDITOR_PERMISSIONS
    permissions |= {"view_results", "download_results"}
    for flag, permission in (("allow_schedule", "schedule_query"), ("allow_public_sharing", "share_dashboard"),
                             ("allow_delete", "delete_assets"), ("allow_ai", "use_ai")):
        if policy["editor"][flag]:
            permissions.add(permission)
        else:
            permissions.discard(permission)
    return sorted(permissions)


def query_limits(user, org):
    if user is not None and not user.is_api_user() and user.role == "admin":
        return None
    limits = policy_for(org)["editor"].copy()
    overrides = (user.details or {}).get("access_limits", {}) if user is not None and not user.is_api_user() else {}
    for field in ("query_timeout_seconds", "max_concurrent_queries"):
        limits[field] = min(limits[field], overrides.get(field, limits[field]))
    return limits


def can_view_sql(user):
    return bool(user and user.is_authenticated and not user.is_api_user() and user.has_permission("view_query"))


def enforce_resource_policy(user, resource, method):
    """Defense in depth for legacy handlers that only checked object ownership."""
    from flask_restful import abort

    if not user.is_authenticated:
        return
    if user.is_api_user():
        readable = {"PublicDashboardResource", "QueryResource", "QueryResultResource", "QueryResultDropdownResource",
                    "QueryDropdownsResource", "JobResource", "CheckPermissionResource"}
        writable = {"QueryResultResource", "JobResource", "EventsResource"}
        if resource not in (readable if method in ("GET", "HEAD", "OPTIONS") else writable):
            abort(403, message="Report tokens only grant access to report results.")
        return
    if user.role == "admin":
        return
    if user.role == "reader":
        readable = {"DashboardListResource", "MyDashboardsResource", "DashboardResource", "DashboardTagsResource",
                    "DashboardFavoriteListResource", "QueryResultResource", "QueryResultDropdownResource",
                    "QueryDropdownsResource", "UserResource", "GroupListResource", "GroupResource",
                    "CheckPermissionResource", "SessionResource", "OrganizationStatusResource"}
        writable = {"QueryResultResource", "DashboardFavoriteResource", "EventsResource", "UserResource",
                    "UserRegenerateApiKeyResource"}
        allowed = readable if method in ("GET", "HEAD", "OPTIONS") else writable
        if resource not in allowed or (method == "DELETE" and resource != "DashboardFavoriteResource"):
            abort(403, message="Readers can browse report results, but cannot view SQL or modify reports.")
    if resource == "DashboardShareResource" and not user.has_permission("share_dashboard"):
        abort(403, message="Public sharing is disabled for your role.")
    if resource == "ObjectPermissionsListResource" and method not in ("GET", "HEAD", "OPTIONS"):
        abort(403, message="Only administrators can change access permissions.")
    if resource in ("QueryResource", "DashboardResource") and method == "DELETE" and not user.has_permission("delete_assets"):
        abort(403, message="Deleting reports and queries is disabled for your role.")
    if resource in ("QueryResource", "DashboardResource") and method == "POST" and not user.has_permission("delete_assets"):
        from flask import request
        body = request.get_json(silent=True)
        if isinstance(body, dict) and body.get("is_archived"):
            abort(403, message="Archiving reports and queries is disabled for your role.")
