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


def _report_objects(user, report):
    from redash.dashboard_access import can_view_dashboard
    if (user is None or user.is_api_user() or user.is_disabled or not isinstance(report, dict)
            or type(report.get("dashboard_id")) is not int or type(report.get("query_id")) is not int
            or not isinstance(report.get("parameters"), dict)):
        raise DataAccessDenied("An active user and saved dashboard report are required.")
    dashboard = models.Dashboard.query.filter_by(id=report["dashboard_id"], org_id=user.org_id).first()
    query = models.Query.query.filter_by(id=report["query_id"], org_id=user.org_id, is_archived=False).first()
    if (dashboard is None or not can_view_dashboard(dashboard, user) or query is None
            or not any(w.visualization and w.visualization.query_rel.id == query.id
                                                     for w in dashboard.widgets)):
        raise DataAccessDenied("This report is not on an accessible dashboard.")
    if report.get("revision") is not None and report["revision"] != dashboard.version:
        raise DataAccessDenied("Dashboard access or content changed.")
    report["revision"] = dashboard.version
    source = query.data_source
    if (source is None or source.org_id != user.org_id or source.paused or is_internal(source)
            or not has_access(query, user, view_only)):
        raise DataAccessDenied("This report's data source is unavailable.")
    return dashboard, query, source


def _authorize(user, report, expected_sql=None, reader=False):
    dashboard, query, source = _report_objects(user, report)
    if reader and source.type not in ("mysql", "pg"):
        raise DataAccessDenied("Reader report refresh currently supports MySQL/MariaDB and PostgreSQL.")
    parameterized = query.parameterized
    if reader and not parameterized.is_safe:
        raise DataAccessDenied("Reader refresh requires typed parameters; free-text SQL parameters are unavailable.")
    try:
        parameterized.apply(report["parameters"])
        if parameterized.missing_params:
            raise ValueError("Missing report parameters.")
        sql = source.query_runner.apply_auto_limit(parameterized.text, query.options.get("apply_auto_limit", False))
        if reader:
            validate_readonly_sql(sql)
    except (ValueError, InvalidParameterError, QueryDetachedFromDataSourceError) as error:
        raise DataAccessDenied("Invalid report parameters or unsupported read-only report SQL.") from error
    if expected_sql is not None and sql != expected_sql:
        raise DataAccessDenied("The saved report changed while refresh was queued.")
    options = source.options.to_dict() if hasattr(source.options, "to_dict") else dict(source.options)
    snapshot = {"purpose": "reader_dashboard_report" if reader else "editor_dashboard_report",
                "org": user.org_id, "source": source.id, "source_type": source.type, "connection": options,
                "dashboard": dashboard.id, "revision": dashboard.version, "query": query.id, "template": query.query_text,
                "options": query.options, "parameters": report["parameters"]}
    key = settings.DATASOURCE_SECRET_KEY
    key = key.encode() if isinstance(key, str) else key
    scope = hmac.new(key, json.dumps(snapshot, sort_keys=True, default=str).encode(), hashlib.sha256).hexdigest()
    return source, sql, scope


def authorize_report(user, report, expected_sql=None):
    if user is None or not is_reader(user):
        raise DataAccessDenied("An active Reader and saved dashboard report are required.")
    return _authorize(user, report, expected_sql, reader=True)


def authorize_editor_report(user, report, expected_sql=None):
    if (user is None or user.is_api_user() or getattr(user, "role", None) not in ("admin", "editor")
            or not user.has_permission("execute_query")):
        raise DataAccessDenied("Your role cannot refresh this dashboard report.")
    source, sql, scope = _authorize(user, report, expected_sql)
    if not has_access(source, user, False):
        raise DataAccessDenied("Full query access is required to refresh this dashboard report.")
    expected = report.get("authorization_scope")
    if expected is not None and expected != scope:
        raise DataAccessDenied("Dashboard report authorization changed.")
    report["authorization_scope"] = scope
    return source, sql, scope


def result_dashboard_allowed(result, user):
    """Never treat a data-source grant as a dashboard/report capability."""
    from redash.dashboard_access import user_can_view_query_dashboard
    from redash.utils import gen_query_hash
    try:
        if user is None or user.is_disabled or user.org_id != result.org_id:
            return False
        if not user.is_api_user() and user.has_permission("admin"):
            return True
        context = getattr(result, "report_context", None)
        if context:
            if not isinstance(context, dict):
                return False
            if context.get("kind") in ("reader", "editor"):
                if user.is_api_user():
                    return False
                if context["kind"] == "reader":
                    _, _, scope = authorize_report(user, dict(context), expected_sql=result.query_text)
                    return scope == result.policy_scope
                _, _, scope = _authorize(user, dict(context), expected_sql=result.query_text)
                return scope == context.get("authorization_scope")
            if context.get("kind") == "adhoc":
                return (not user.is_api_user() and not is_reader(user)
                        and context.get("user_id") == user.id)
            if context.get("kind") != "saved" or type(context.get("query_id")) is not int:
                return False
            query = models.Query.query.filter_by(id=context["query_id"], org_id=user.org_id, is_archived=False).first()
            if (query is None or query.data_source_id != result.data_source_id
                    or not user_can_view_query_dashboard(query, user)):
                return False
            if not is_reader(user):
                return True
            # Without original parameters only a verified current pointer/template
            # is a Reader capability, even when server provenance names a query.
            candidates = [query]
        else:
            candidates = models.Query.query.filter_by(org_id=user.org_id, latest_query_data_id=result.id,
                                                       is_archived=False).all()
        for query in candidates:
            if (query.latest_query_data_id != result.id or query.data_source_id != result.data_source_id
                    or not user_can_view_query_dashboard(query, user)
                    or "{{" in query.query_text or "}}" in query.query_text):
                continue
            text = query.data_source.query_runner.apply_auto_limit(query.query_text,
                    query.options.get("apply_auto_limit", False))
            if gen_query_hash(text) == result.query_hash:
                return True
        return False
    except (DataAccessDenied, ValueError, TypeError, KeyError, AttributeError):
        return False
