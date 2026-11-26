# Copyright (c) 2026 Slavcho Spasenoski. Original Bi-Dash contributions.
# SPDX-License-Identifier: BSD-2-Clause
"""Authorization and provenance for Dataset-backed saved queries."""

import hashlib
import hmac

from redash import models, settings
from redash.data_access_runtime import DataAccessDenied, access_scope
from redash.dataset_access import can_view_dataset
from redash.dataset_sql import preserve_dataset_numbers
from redash.query_revisions import snapshot_query
from redash.utils import json_dumps


def is_dataset_report(query):
    return bool(query is not None and (query.options or {}).get("dataset_report") is True)


def dataset_report_context(query, user, dashboard_id=None):
    """Bind a report to the current actor, source, dashboard and revision."""
    from redash.dashboard_access import user_can_view_query_dashboard
    from redash.permissions import has_access, view_only

    if (not settings.DATASETS_ENABLED or not is_dataset_report(query) or user is None
            or user.is_api_user() or user.is_disabled or query.is_archived or user.org_id != query.org_id):
        raise DataAccessDenied("This Dataset report is unavailable.")
    link = models.DatasetReport.query.filter_by(query_id=query.id).first()
    revision = models.db.session.get(models.DatasetRevision, link.dataset_revision_id) if link else None
    dataset = models.Dataset.query.filter_by(
        id=revision.dataset_id, org_id=query.org_id, archived=False,
    ).first() if revision else None
    if dataset is None:
        raise DataAccessDenied("The published Dataset is unavailable.")
    latest = (models.DatasetRevision.query.filter_by(dataset_id=dataset.id)
              .order_by(models.DatasetRevision.revision.desc()).first())
    if latest is None or latest.id != revision.id:
        raise DataAccessDenied("The Dataset was republished. Rebuild this report from its current version.")
    base = models.Query.query.filter_by(
        id=dataset.base_query_id, org_id=query.org_id, is_archived=False,
    ).first()
    base_revision = models.QueryRevision.query.filter_by(
        id=revision.base_revision_id, query_id=dataset.base_query_id, org_id=query.org_id,
    ).first()
    source = query.data_source
    if (base is None or is_dataset_report(base) or base_revision is None
            or base_revision.snapshot != snapshot_query(base) or source is None
            or base.data_source_id != source.id or source.org_id != user.org_id or source.paused
            or not has_access(source, user, view_only)):
        raise DataAccessDenied("The Dataset report source or base Query changed.")
    if not user_can_view_query_dashboard(query, user, dashboard_id=dashboard_id):
        raise DataAccessDenied("This report is not accessible.")
    if user.role != "reader" and not can_view_dataset(dataset, user):
        raise DataAccessDenied("This Dataset is no longer assigned to you.")
    dashboard = (models.Dashboard.query.filter_by(id=dashboard_id, org_id=query.org_id).first()
                 if dashboard_id is not None else None)
    context = {
        "dataset_id": dataset.id, "dataset_revision_id": revision.id,
        "query_id": query.id, "user_id": user.id, "dashboard_id": dashboard_id,
    }
    options = source.options.to_dict() if hasattr(source.options, "to_dict") else dict(source.options)
    binding = json_dumps([context, dashboard.version if dashboard else None, query.version, query.query_text, link.spec, user.role,
                          source.id, options, access_scope(source, user)], sort_keys=True)
    key = settings.DATASOURCE_SECRET_KEY
    if isinstance(key, str):
        key = key.encode()
    context["scope"] = hmac.new(key, binding.encode(), hashlib.sha256).hexdigest()
    return context


def dataset_result_allowed(result, user):
    context = result.report_context.get("dataset_report")
    if not isinstance(context, dict) or user is None or context.get("user_id") != user.id:
        return False
    query = models.Query.query.filter_by(
        id=context.get("query_id"), org_id=user.org_id, data_source_id=result.data_source_id,
    ).first()
    try:
        return (query is not None and query.query_text == result.query_text
                and (user.role != "reader" or context.get("dashboard_id") is not None)
                and dataset_report_context(query, user, context.get("dashboard_id")) == context)
    except (DataAccessDenied, ValueError, TypeError, KeyError, AttributeError):
        return False


def run_dataset_report(query, user, context):
    from redash.data_access_runtime import resolved_policy
    from redash.data_access_shared import run_shared_query

    if dataset_report_context(query, user, context.get("dashboard_id")) != context:
        raise DataAccessDenied("Dataset report access changed before execution.")
    link = models.DatasetReport.query.filter_by(query_id=query.id).one()
    revision = models.db.session.get(models.DatasetRevision, link.dataset_revision_id)
    base = models.db.session.get(models.QueryRevision, revision.base_revision_id)
    source = query.data_source
    policy = resolved_policy(source, user) or {"unrestricted": True}
    data, error = run_shared_query(
        source, policy, base.snapshot["query"],
        dataset_report=(revision.snapshot["fields"], link.spec, query.query_text),
    )
    return preserve_dataset_numbers(data) if data is not None else None, error
