# Copyright (c) 2026 Slavcho Spasenoski. Original Bi-Dash contributions.
# SPDX-License-Identifier: BSD-2-Clause
"""A Reader may refresh a saved dashboard report, never submit SQL."""
import hashlib
import hmac
import json

from redash import models, settings
from redash.ai_dashboard import validate_readonly_sql
from redash.data_access_runtime import DataAccessDenied, is_internal, is_reader
from redash.permissions import has_access, view_only
from redash.models.parameterized_query import InvalidParameterError, QueryDetachedFromDataSourceError


def authorize_report(user, report, expected_sql=None):
    if (user is None or not is_reader(user) or user.is_disabled or not isinstance(report, dict)
            or type(report.get("dashboard_id")) is not int or type(report.get("query_id")) is not int
            or not isinstance(report.get("parameters"), dict)):
        raise DataAccessDenied("An active Reader and saved dashboard report are required.")
    dashboard = models.Dashboard.all(user.org, user.group_ids, user.id).filter(
        models.Dashboard.id == report["dashboard_id"]).first()
    query = models.Query.query.filter_by(id=report["query_id"], org_id=user.org_id, is_archived=False).first()
    if (dashboard is None or query is None or not any(w.visualization and w.visualization.query_rel.id == query.id
                                                     for w in dashboard.widgets)):
        raise DataAccessDenied("This report is not on an accessible dashboard.")
    source = query.data_source
    if (source is None or source.org_id != user.org_id or source.paused or is_internal(source)
            or not has_access(query, user, view_only)):
        raise DataAccessDenied("This report's data source is unavailable.")
    if source.type not in ("mysql", "pg"):
        raise DataAccessDenied("Reader report refresh currently supports MySQL/MariaDB and PostgreSQL.")
    parameterized = query.parameterized
    if not parameterized.is_safe:
        raise DataAccessDenied("Reader refresh requires typed parameters; free-text SQL parameters are unavailable.")
    try:
        parameterized.apply(report["parameters"])
        if parameterized.missing_params:
            raise ValueError("Missing report parameters.")
        sql = source.query_runner.apply_auto_limit(parameterized.text, query.options.get("apply_auto_limit", False))
        validate_readonly_sql(sql)
    except (ValueError, InvalidParameterError, QueryDetachedFromDataSourceError) as error:
        raise DataAccessDenied("Invalid report parameters or unsupported read-only report SQL.") from error
    if expected_sql is not None and sql != expected_sql:
        raise DataAccessDenied("The saved report changed while refresh was queued.")
    options = source.options.to_dict() if hasattr(source.options, "to_dict") else dict(source.options)
    snapshot = {"purpose": "reader_dashboard_report", "org": user.org_id, "source": source.id, "source_type": source.type, "connection": options,
                "dashboard": dashboard.id, "query": query.id, "template": query.query_text,
                "options": query.options, "parameters": report["parameters"]}
    key = settings.DATASOURCE_SECRET_KEY
    key = key.encode() if isinstance(key, str) else key
    scope = hmac.new(key, json.dumps(snapshot, sort_keys=True, default=str).encode(), hashlib.sha256).hexdigest()
    return source, sql, scope
