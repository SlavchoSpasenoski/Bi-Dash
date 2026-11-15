"""
This will eventually replace all the `to_dict` methods of the different model
classes we have. This will ensure cleaner code and better
separation of concerns.
"""

from flask_login import current_user
from funcy import project
from rq.job import JobStatus
from rq.timeouts import JobTimeoutException

from redash import models
from redash.access_policy import can_view_sql
from redash.models.parameterized_query import ParameterizedQuery
from redash.permissions import has_access, view_only
from redash.serializers.query_result import (
    serialize_query_result,
    serialize_query_result_to_dsv,
    serialize_query_result_to_xlsx,
)


def public_widget(widget):
    res = {
        "id": widget.id,
        "width": widget.width,
        "options": widget.options,
        "text": widget.text,
        "updated_at": widget.updated_at,
        "created_at": widget.created_at,
    }

    v = widget.visualization
    if v and v.id:
        res["visualization"] = {
            "type": v.type,
            "name": v.name,
            "description": v.description,
            "options": v.options,
            "updated_at": v.updated_at,
            "created_at": v.created_at,
            "query": {
                "id": v.query_rel.id,
                "name": v.query_rel.name,
                "description": v.query_rel.description,
                "options": v.query_rel.options,
            },
        }

    return res


def public_dashboard(dashboard):
    dashboard_dict = project(
        serialize_dashboard(dashboard, with_favorite_state=False),
        ("name", "layout", "dashboard_filters_enabled", "updated_at", "created_at", "options"),
    )

    widget_list = (
        models.Widget.query.filter(models.Widget.dashboard_id == dashboard.id)
        .outerjoin(models.Visualization)
        .outerjoin(models.Query)
    )

    dashboard_dict["widgets"] = [public_widget(w) for w in widget_list]
    return dashboard_dict


class Serializer:
    pass


class QuerySerializer(Serializer):
    def __init__(self, object_or_list, **kwargs):
        self.object_or_list = object_or_list
        self.options = kwargs

    def serialize(self):
        if isinstance(self.object_or_list, models.Query):
            result = serialize_query(self.object_or_list, **self.options)
            if self.options.get("with_favorite_state", True) and not current_user.is_api_user():
                result["is_favorite"] = models.Favorite.is_favorite(current_user.id, self.object_or_list)
        else:
            from redash.data_access_runtime import can_access_source, is_protected
            queries = [query for query in self.object_or_list if has_access(query, current_user, view_only)]
            result = [serialize_query(query, **self.options) for query in queries]
            if self.options.get("with_favorite_state", True):
                favorites = models.Favorite.query.filter(
                    models.Favorite.object_id.in_([o.id for o in queries]),
                    models.Favorite.object_type == "Query",
                    models.Favorite.user_id == current_user.id,
                )
                favorites_dict = {fav.object_id: fav for fav in favorites}

                for query in result:
                    favorite = favorites_dict.get(query["id"])
                    query["is_favorite"] = favorite is not None
                    if favorite:
                        query["starred_at"] = favorite.created_at

        return result


def serialize_query(
    query,
    with_stats=False,
    with_visualizations=False,
    with_user=True,
    with_last_modified_by=True,
):
    from redash.data_access_runtime import can_read_all_scopes, DataAccessDenied, access_scope, can_access_source, is_protected, resolve_source
    from redash.reader_reports import result_dashboard_allowed
    if current_user and not has_access(query, current_user, view_only):
        from flask_restful import abort
        abort(403, message="This report is not on an assigned dashboard.")
    if query.data_source is not None and can_access_source(query.data_source, current_user) is False:
        from flask_restful import abort
        abort(403, message="This query's data source is no longer accessible.")
    protected = query.data_source is not None and is_protected(query.data_source)
    scoped_result = None
    if protected:
        if not has_access(query, current_user, view_only):
            from flask_restful import abort
            abort(403, message="This query belongs to a different or retired access profile.")
        try:
            source = resolve_source(query.data_source, current_user)
            text = source.query_runner.apply_auto_limit(query.query_text, query.options.get("apply_auto_limit", False))
            scoped_result = models.QueryResult.get_latest(source, text, -1,
                policy_scope=access_scope(source, current_user), any_policy_scope=can_read_all_scopes(current_user))
            if scoped_result is not None and not result_dashboard_allowed(scoped_result, current_user):
                scoped_result = None
        except DataAccessDenied:
            pass
    display_result = scoped_result if protected else query.latest_query_data
    if display_result is not None and current_user and not result_dashboard_allowed(display_result, current_user):
        display_result = None
    d = {
        "id": query.id,
        "latest_query_data_id": display_result.id if display_result else None,
        "name": query.name,
        "description": query.description,
        "query": query.query_text,
        "query_hash": query.query_hash,
        "schedule": query.schedule,
        "api_key": query.api_key,
        "is_archived": query.is_archived,
        "is_draft": query.is_draft,
        "updated_at": query.updated_at,
        "created_at": query.created_at,
        "data_source_id": query.data_source_id,
        "options": query.options,
        "version": query.version,
        "tags": query.tags or [],
        "is_safe": query.parameterized.is_safe,
    }

    if protected:
        d["latest_query_data_id"] = scoped_result.id if scoped_result else None
        d.pop("api_key", None)
        d["schedule"] = None

    if with_user:
        d["user"] = query.user.to_dict()
    else:
        d["user_id"] = query.user_id

    if with_last_modified_by:
        d["last_modified_by"] = query.last_modified_by.to_dict() if query.last_modified_by is not None else None
    else:
        d["last_modified_by_id"] = query.last_modified_by_id

    if with_stats:
        if protected:
            d["retrieved_at"] = scoped_result.retrieved_at if scoped_result else None
            d["runtime"] = scoped_result.runtime if scoped_result else None
        elif display_result is not None:
            d["retrieved_at"] = display_result.retrieved_at
            d["runtime"] = display_result.runtime
        else:
            d["retrieved_at"] = None
            d["runtime"] = None

    if with_visualizations:
        d["visualizations"] = [serialize_visualization(vis, with_query=False) for vis in query.visualizations]

    if not can_view_sql(current_user):
        for key in ("query", "query_hash", "api_key", "schedule"):
            d.pop(key, None)
    elif not current_user.has_permission("admin") and current_user.id != query.user_id:
        d.pop("api_key", None)

    return d


def serialize_visualization(object, with_query=True):
    d = {
        "id": object.id,
        "type": object.type,
        "name": object.name,
        "description": object.description,
        "options": object.options,
        "updated_at": object.updated_at,
        "created_at": object.created_at,
    }

    if with_query:
        d["query"] = serialize_query(object.query_rel)

    return d


def serialize_widget(object):
    d = {
        "id": object.id,
        "width": object.width,
        "options": object.options,
        "dashboard_id": object.dashboard_id,
        "text": object.text,
        "updated_at": object.updated_at,
        "created_at": object.created_at,
    }

    if object.visualization and object.visualization.id:
        d["visualization"] = serialize_visualization(object.visualization)

    return d


def serialize_alert(alert, full=True):
    d = {
        "id": alert.id,
        "name": alert.name,
        "options": alert.options,
        "state": alert.state,
        "last_triggered_at": alert.last_triggered_at,
        "updated_at": alert.updated_at,
        "created_at": alert.created_at,
        "rearm": alert.rearm,
    }

    if full:
        d["query"] = serialize_query(alert.query_rel)
        d["user"] = alert.user.to_dict()
    else:
        d["query_id"] = alert.query_id
        d["user_id"] = alert.user_id

    return d


def serialize_dashboard(obj, with_widgets=False, user=None, with_favorite_state=True):
    layout = obj.layout

    widgets = []

    if with_widgets:
        for w in obj.widgets:
            if w.visualization_id is None:
                widgets.append(serialize_widget(w))
            elif user and has_access(w.visualization.query_rel, user, view_only):
                widgets.append(serialize_widget(w))
            else:
                # Never serialize the inaccessible query even temporarily:
                # its authorization guard would abort the whole dashboard.
                widget = {"id": w.id, "width": w.width, "dashboard_id": w.dashboard_id,
                          "options": {"position": (w.options or {}).get("position", {})},
                          "created_at": w.created_at, "updated_at": w.updated_at}
                widget["restricted"] = True
                widgets.append(widget)
    else:
        widgets = None

    d = {
        "id": obj.id,
        "slug": obj.name_as_slug,
        "name": obj.name,
        "user_id": obj.user_id,
        "user": {
            "id": obj.user.id,
            "name": obj.user.name,
            "email": obj.user.email,
            "profile_image_url": obj.user.profile_image_url,
        },
        "layout": layout,
        "dashboard_filters_enabled": obj.dashboard_filters_enabled,
        "widgets": widgets,
        "options": obj.options,
        "is_archived": obj.is_archived,
        "is_draft": obj.is_draft,
        "tags": obj.tags or [],
        "updated_at": obj.updated_at,
        "created_at": obj.created_at,
        "version": obj.version,
    }
    actor = user or current_user
    if actor:
        from redash.dashboard_access import can_edit_dashboard, can_manage_dashboard_access
        d["can_edit"] = can_edit_dashboard(obj, actor)
        d["can_manage_access"] = can_manage_dashboard_access(obj, actor)
    d["public_sharing_enabled"] = False

    return d


class DashboardSerializer(Serializer):
    def __init__(self, object_or_list, **kwargs):
        self.object_or_list = object_or_list
        self.options = kwargs

    def serialize(self):
        if isinstance(self.object_or_list, models.Dashboard):
            result = serialize_dashboard(self.object_or_list, **self.options)
            if self.options.get("with_favorite_state", True) and not current_user.is_api_user():
                result["is_favorite"] = models.Favorite.is_favorite(current_user.id, self.object_or_list)
        else:
            result = [serialize_dashboard(obj, **self.options) for obj in self.object_or_list]
            if self.options.get("with_favorite_state", True):
                dashboards = list(self.object_or_list)
                favorites = models.Favorite.query.filter(
                    models.Favorite.object_id.in_([o.id for o in dashboards]),
                    models.Favorite.object_type == "Dashboard",
                    models.Favorite.user_id == current_user.id,
                )
                favorites_dict = {fav.object_id: fav for fav in favorites}

                for query in result:
                    favorite = favorites_dict.get(query["id"])
                    query["is_favorite"] = favorite is not None
                    if favorite:
                        query["starred_at"] = favorite.created_at

        return result


def serialize_job(job):
    # TODO: this is mapping to the old Job class statuses. Need to update the client side and remove this
    STATUSES = {
        JobStatus.QUEUED: 1,
        JobStatus.STARTED: 2,
        JobStatus.FINISHED: 3,
        JobStatus.FAILED: 4,
        JobStatus.CANCELED: 5,
        JobStatus.DEFERRED: 6,
        JobStatus.SCHEDULED: 7,
        JobStatus.STOPPED: 4,
    }

    job_status = job.get_status()
    if job.is_started:
        updated_at = job.started_at or 0
    else:
        updated_at = 0

    status = STATUSES[job_status]
    result = query_result_id = None

    if job.is_cancelled:
        error = "Query cancelled by user."
        status = 4
    elif isinstance(job.result, Exception):
        error = str(job.result) if can_view_sql(current_user) else "Report refresh failed. Contact an editor."
        status = 4
    elif isinstance(job.result, dict) and "error" in job.result:
        error = job.result["error"]
        status = 4
    else:
        error = ""
        result = query_result_id = job.result

    return {
        "job": {
            "id": job.id,
            "updated_at": updated_at,
            "status": status,
            "error": error,
            "result": result,
            "query_result_id": query_result_id,
        }
    }
