from flask import request
from flask_login import current_user, login_required
from flask_restful import abort

from redash import models
from redash.handlers import routes
from redash.handlers.base import (
    get_object_or_404,
    org_scoped_rule,
    record_event,
)
from redash.handlers.static import render_index
from redash.security import csp_allows_embeding

from .authentication import current_org


@routes.route(
    org_scoped_rule("/embed/query/<query_id>/visualization/<visualization_id>"),
    methods=["GET"],
)
@login_required
@csp_allows_embeding
def embed(query_id, visualization_id, org_slug=None):
    from redash.permissions import require_access, view_only
    if current_user.is_api_user():
        abort(403, message="Anonymous report links are disabled. Sign in with assigned access.")
    visualization = get_object_or_404(models.Visualization.get_by_id_and_org, visualization_id, current_org)
    if str(visualization.query_id) != str(query_id):
        abort(404)
    require_access(visualization.query_rel, current_user, view_only)
    record_event(
        current_org,
        current_user._get_current_object(),
        {
            "action": "view",
            "object_id": visualization_id,
            "object_type": "visualization",
            "query_id": query_id,
            "embed": True,
            "referer": request.headers.get("Referer"),
        },
    )
    return render_index()


@routes.route(org_scoped_rule("/public/dashboards/<token>"), methods=["GET"])
@login_required
@csp_allows_embeding
def public_dashboard(token, org_slug=None):
    abort(403, message="Public dashboard links are disabled. Sign in with assigned access.")
