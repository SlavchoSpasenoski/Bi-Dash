from flask_login import current_user, login_required

from redash import models
from redash.authentication import current_org
from redash.handlers import routes
from redash.handlers.base import json_response, org_scoped_rule


@routes.route(org_scoped_rule("/api/organization/status"), methods=["GET"])
@login_required
def organization_status(org_slug=None):
    from redash.handlers.alerts import readable_alerts
    from redash.handlers.queries import filter_readable_report_queries

    queries = models.Query.all_queries(current_user.group_ids, current_user.id, include_drafts=True)
    queries = filter_readable_report_queries(queries.filter(models.Query.org_id == current_org.id), current_user)
    counters = {
        "users": models.User.all(current_org).count(),
        "alerts": len(readable_alerts(current_user, current_org)),
        "data_sources": models.DataSource.all(current_org, group_ids=current_user.group_ids).count(),
        "queries": queries.count(),
        "dashboards": models.Dashboard.all(current_org, current_user.group_ids, current_user.id,
                                           include_all=current_user.has_permission("admin")).count(),
    }

    return json_response(dict(object_counters=counters))
