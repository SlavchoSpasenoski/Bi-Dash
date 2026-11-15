"""Read-only administrator explanation of the existing authorization decisions.

Never opens source connections, runs SQL, calls AI or returns connection options.
"""
from redash import models
from redash.access_policy import query_limits
from redash.dashboard_access import can_edit_dashboard, can_manage_dashboard_access, can_view_dashboard
from redash.data_access_runtime import DataAccessDenied, is_protected, resolved_policy
from redash.permissions import has_access, has_access_to_groups, not_view_only, view_only
from redash.utils import utcnow


def decision(allowed, reason):
    return {"allowed": bool(allowed), "reason": reason}


def source_access(source, user, groups):
    active = not user.is_disabled
    matching = [g for g in groups if g.id in source.groups]
    group_view = has_access_to_groups(source, user, view_only)
    group_execute = has_access_to_groups(source, user, not_view_only)
    policy, error = None, None
    try:
        policy = resolved_policy(source, user)
    except DataAccessDenied as exc:
        error = str(exc)
    read = active and group_view and error is None
    reason = ("Account is disabled." if not active else
              "No source access through the user's groups." if not group_view else error if error else
              "Administrator source access." if user.role == "admin" else
              "Assigned-report access; Editor column rules do not apply to Readers." if user.role == "reader" else
              "Matching group table/column allowlists are combined." if policy else
              "No table/column rule sets; source Groups permissions apply.")
    # Do not imply that a source-level grant proves connector/SQL/connection health.
    execute = read and group_execute and user.role != "reader" and user.has_permission("execute_query") and not source.paused
    execute_reason = (reason if not read else "Readers can refresh assigned reports, not submit SQL." if user.role == "reader" else
                      "Source is paused." if source.paused else "Source groups grant View Only." if not group_execute else
                      "The role or group does not grant SQL execution." if not user.has_permission("execute_query") else
                      "SQL permission granted; connector and statement validation still apply.")
    return {
        "id": source.id, "name": source.name, "type": source.type, "paused": bool(source.paused),
        "groups": [{"id": g.id, "name": g.name, "level": "view_only" if source.groups[g.id] else "full"} for g in matching],
        "mode": "restricted" if is_protected(source) else "groups",
        "view": decision(read, reason), "execute": decision(execute, execute_reason),
        "tables": policy["rules"] if read and policy and not policy["unrestricted"] else [],
        "table_scope": "allowlist" if read and policy and not policy["unrestricted"] else
                       "report_output" if read and user.role == "reader" else "all" if read else "none",
    }


def user_effective_access(user):
    groups = models.Group.query.filter(models.Group.org_id == user.org_id, models.Group.id.in_(user.group_ids)).order_by(models.Group.name).all()
    active = not user.is_disabled
    actions = []
    for key, label, permission in (
        ("admin", "Manage users, roles and data rules", "admin"),
        ("create_dashboard", "Create dashboards", "create_dashboard"),
        ("create_query", "Create queries", "create_query"),
        ("sql", "Submit SQL", "execute_query"), ("ai", "Use AI", "use_ai"),
        ("download", "Download report results", "download_results"),
        ("schedule", "Schedule queries", "schedule_query"),
        ("delete", "Archive owned queries and dashboards", "delete_assets"),
    ):
        allowed = active and user.has_permission(permission)
        actions.append({"key": key, "label": label, **decision(allowed,
            "Account is disabled." if not active else
            "Role/group permission granted; resource-specific checks still apply." if allowed else
            "Not granted by the role, organization policy or group permissions.")})
    actions.append({"key": "public", "label": "Anonymous public links", **decision(False, "Public dashboard and report links are disabled for all roles.")})
    sources = [source_access(s, user, groups) for s in models.DataSource.query.filter_by(org_id=user.org_id).order_by(models.DataSource.name)]
    source_map = {s["id"]: s for s in sources}
    dashboards = []
    for board in models.Dashboard.query.filter_by(org_id=user.org_id).order_by(models.Dashboard.name):
        visible = can_view_dashboard(board, user)
        granting_groups = (models.Group.query.join(models.DashboardGroupReadGrant,
            models.DashboardGroupReadGrant.group_id == models.Group.id).filter(models.Group.org_id == user.org_id,
            models.Group.id.in_(user.group_ids), models.DashboardGroupReadGrant.dashboard_id == board.id).all())
        direct = models.DashboardReadGrant.query.filter_by(dashboard_id=board.id, user_id=user.id).first() is not None
        reason = ("Account is disabled." if not active else "Dashboard is archived." if board.is_archived else
                  "Administrator visibility." if user.has_permission("admin") else
                  "Dashboard owner." if user.role == "editor" and board.user_id == user.id else
                  "Drafts are limited to the Editor/Admin owner and administrators." if board.is_draft else
                  ("Explicit viewer assignment. " if direct else "") +
                  ("Via groups: " + ", ".join(g.name for g in granting_groups) + "." if granting_groups else "")
                  if visible else "Dashboard has not been assigned to this user or their groups.")
        widgets = []
        for widget in board.widgets:
            query = widget.visualization.query_rel if widget.visualization else None
            if query is None:
                continue
            source = source_map.get(query.data_source_id)
            allowed = visible and has_access(query, user, view_only)
            widget_reason = (reason if not visible else "The query's source is unavailable." if source is None else
                             source["view"]["reason"] if not source["view"]["allowed"] else
                             "Query definition is authorized; cached results and refresh are checked separately." if allowed else
                             "Query dependencies or parameter template are outside the current permissions or supported sharing subset.")
            widgets.append({"id": widget.id, "query_id": query.id, "name": query.name,
                            "source_id": query.data_source_id, **decision(allowed, widget_reason)})
        dashboards.append({"id": board.id, "name": board.name, "owner_id": board.user_id,
            "state": "archived" if board.is_archived else "draft" if board.is_draft else "published",
            "view": decision(visible, reason),
            "edit": decision(can_edit_dashboard(board, user), "Only an active Editor/Admin owner with edit permission can change content; archived dashboards require restoration."),
            "share": decision(can_manage_dashboard_access(board, user), "Administrators manage all viewers; Editor owners manage their own. New grants require a published dashboard."),
            "widgets": widgets})
    limits = query_limits(user, user.org)
    return {"generated_at": utcnow().isoformat(), "user": {"id": user.id, "name": user.name, "email": user.email, "role": user.role, "disabled": user.is_disabled},
            "groups": [{"id": g.id, "name": g.name} for g in groups], "actions": actions,
            "limits": {k: limits[k] for k in ("query_timeout_seconds", "max_concurrent_queries")} if limits else None,
            "sources": sources, "dashboards": dashboards,
            "notes": ["This is a read-only permissions snapshot. Reload after changing roles, groups, rules or dashboard viewers.",
                      "No source connection, SQL execution, AI request or cached-result retrieval is performed.",
                      "Dashboard visibility does not grant source access. Matching group allowlists are combined; there are no overriding deny rules.",
                      "AI additionally requires an authorized supported source and provider configuration. Editors use permitted MySQL/MariaDB names only; Readers cannot use AI.",
                      "Reader report refresh uses saved SQL and safe typed parameters on supported connectors; result provenance and fresh permissions are checked at execution."]}
