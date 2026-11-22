# Copyright (c) 2026 Slavcho Spasenoski. Original Bi-Dash contributions.
# SPDX-License-Identifier: BSD-2-Clause
"""Dashboard read grants are independent from source access and content ownership."""
from flask_restful import abort
from sqlalchemy import and_, false, or_


def _active_user(user, org_id=None):
    return bool(user is not None and not user.is_api_user() and not user.is_disabled
                and (org_id is None or user.org_id == org_id))


def dashboard_visibility_clause(user, include_archived=False):
    from redash import models
    dashboard = models.Dashboard
    if not _active_user(user):
        return false()
    bounds = [dashboard.org_id == user.org_id]
    if not include_archived:
        bounds.append(dashboard.is_archived.is_(False))
    if not user.has_permission("admin"):
        granted = models.db.session.query(models.DashboardReadGrant.dashboard_id).filter(
            models.DashboardReadGrant.dashboard_id == dashboard.id,
            models.DashboardReadGrant.user_id == user.id).exists()
        group_granted = models.db.session.query(models.DashboardGroupReadGrant.dashboard_id).join(
            models.Group, models.Group.id == models.DashboardGroupReadGrant.group_id).filter(
            models.DashboardGroupReadGrant.dashboard_id == dashboard.id,
            models.Group.org_id == user.org_id, models.Group.id.in_(user.group_ids)).exists()
        owner = dashboard.user_id == user.id if user.role == "editor" else false()
        bounds.append(or_(owner, and_(dashboard.is_draft.is_(False), or_(granted, group_granted))))
    return and_(*bounds)


def filter_dashboard_query(query, user, include_archived=False):
    return query.filter(dashboard_visibility_clause(user, include_archived=include_archived))


def can_view_dashboard(dashboard, user, include_archived=False):
    from redash import models
    if (dashboard is None or not _active_user(user, dashboard.org_id)
            or (dashboard.is_archived and not include_archived)):
        return False
    if user.has_permission("admin") or (user.role == "editor" and dashboard.user_id == user.id):
        return True
    if dashboard.is_draft:
        return False
    return (models.DashboardReadGrant.query.filter_by(dashboard_id=dashboard.id, user_id=user.id).first() is not None
            or models.DashboardGroupReadGrant.query.join(models.Group).filter(
                models.DashboardGroupReadGrant.dashboard_id == dashboard.id,
                models.Group.org_id == user.org_id, models.Group.id.in_(user.group_ids)).first() is not None)


def can_edit_dashboard(dashboard, user):
    return bool(dashboard is not None and _active_user(user, dashboard.org_id)
                and (user.role == "admin" or (user.role == "editor" and dashboard.user_id == user.id))
                and user.has_permission("edit_dashboard"))


def can_manage_dashboard_access(dashboard, user):
    return bool(dashboard is not None and _active_user(user, dashboard.org_id)
                and (user.has_permission("admin") or
                     (user.role == "editor" and dashboard.user_id == user.id)))


def require_dashboard_view(dashboard, user):
    if not can_view_dashboard(dashboard, user):
        abort(403, message="This dashboard has not been assigned to you, or is not available.")


def require_dashboard_edit(dashboard, user):
    if not can_edit_dashboard(dashboard, user):
        abort(403, message="Only an administrator or the dashboard owner can edit its content.")


def query_readable_clause(user):
    """Query access still requires its independent source/column authorization."""
    from redash import models
    if not _active_user(user):
        return false()
    if user.has_permission("admin"):
        return models.Query.org_id == user.org_id
    attached = models.db.session.query(models.Widget.id).join(models.Visualization).filter(
        models.Visualization.query_id == models.Query.id).exists()
    readable = models.db.session.query(models.Widget.id).join(models.Visualization).join(
        models.Dashboard, models.Widget.dashboard_id == models.Dashboard.id).filter(
            models.Visualization.query_id == models.Query.id,
            dashboard_visibility_clause(user)).exists()
    if user.role == "reader":
        return and_(models.Query.org_id == user.org_id, readable)
    return and_(models.Query.org_id == user.org_id,
                or_(models.Query.user_id == user.id, ~attached, readable))


def user_can_view_query_dashboard(query, user, dashboard_id=None):
    from redash import models
    if query is None or not _active_user(user, query.org_id):
        return False
    if dashboard_id is not None:
        if type(dashboard_id) is not int:
            return False
        dashboard = models.Dashboard.query.filter_by(id=dashboard_id, org_id=user.org_id).first()
        return bool(can_view_dashboard(dashboard, user) and models.Widget.query.join(models.Visualization).filter(
            models.Widget.dashboard_id == dashboard_id, models.Visualization.query_id == query.id).first())
    return models.Query.query.filter(models.Query.id == query.id, query_readable_clause(user)).first() is not None
