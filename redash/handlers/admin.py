from flask import abort, request, session
from flask_login import current_user, login_required

from redash import models, redis_connection
from redash.authentication import current_org
from redash.handlers import routes
from redash.handlers.base import json_response, record_event, org_scoped_rule
from redash.monitor import rq_status
from redash.permissions import require_super_admin, require_permission
from redash.serializers import QuerySerializer
from redash.utils import json_loads


@routes.route("/api/admin/queries/outdated", methods=["GET"])
@require_super_admin
@login_required
def outdated_queries():
    manager_status = redis_connection.hgetall("redash:status")
    query_ids = json_loads(manager_status.get("query_ids", "[]"))
    if query_ids:
        outdated_queries = (
            models.Query.query.outerjoin(models.QueryResult)
            .filter(models.Query.id.in_(query_ids))
            .order_by(models.Query.created_at.desc())
        )
    else:
        outdated_queries = []

    record_event(
        current_org,
        current_user._get_current_object(),
        {
            "action": "list",
            "object_type": "outdated_queries",
        },
    )

    response = {
        "queries": QuerySerializer(outdated_queries, with_stats=True, with_last_modified_by=False).serialize(),
        "updated_at": manager_status["last_refresh_at"],
    }
    return json_response(response)


@routes.route("/api/admin/queries/rq_status", methods=["GET"])
@require_super_admin
@login_required
def queries_rq_status():
    record_event(
        current_org,
        current_user._get_current_object(),
        {"action": "list", "object_type": "rq_status"},
    )

    return json_response(rq_status())


@routes.route(org_scoped_rule("/api/admin/monitoring"), methods=["GET"])
@login_required
@require_permission("admin")
def monitoring_status(org_slug=None):
    if (current_user.is_api_user() or current_user.is_disabled or current_user.org_id != current_org.id
            or session.get('_user_id') != current_user.get_id()
            or 'api_key' in request.args or request.headers.get('Authorization')):
        abort(403)
    def number(name, default, maximum):
        try:
            value = int(request.args.get(name, default))
        except (ValueError, TypeError):
            abort(400)
        if not 1 <= value <= maximum:
            abort(400)
        return value
    from redash.operational_monitoring import get_snapshot
    response = json_response(get_snapshot(current_org, current_user,
        users_page=number('users_page', 1, 10000), jobs_page=number('jobs_page', 1, 10000),
        page_size=number('page_size', 25, 100)))
    response.headers['Cache-Control'] = 'private, no-store'
    return response
