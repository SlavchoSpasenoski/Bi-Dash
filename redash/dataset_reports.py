# Copyright (c) 2026 Slavcho Spasenoski. Original Bi-Dash contributions.
# SPDX-License-Identifier: BSD-2-Clause
"""Resolve and attach structured Dataset reports. Callers own the transaction."""

from copy import deepcopy

from redash import models
from redash.data_access_runtime import resolved_policy
from redash.dataset_access import can_view_dataset
from redash.dataset_sql import DatasetSqlError, _check_field_names
from redash.datasets import DatasetConflict
from redash.models.dataset_reports import DatasetReport
from redash.permissions import has_access, not_view_only
from redash.query_revisions import snapshot_query


def _validate_report_spec(spec, fields):
    if not isinstance(spec, dict) or set(spec) != {"group_by", "metrics", "filters"}:
        raise DatasetSqlError("Provide grouping, metrics and filters for the report.")
    groups, metrics, filters = spec["group_by"], spec["metrics"], spec["filters"]
    if (not isinstance(groups, list) or len(groups) > 4 or not isinstance(metrics, dict)
            or len(metrics) > 8 or not isinstance(filters, list) or len(filters) > 8):
        raise DatasetSqlError("Use up to four fields, eight metrics and eight filters.")

    references = list(groups)
    for metric in metrics.values():
        if (not isinstance(metric, dict) or set(metric) != {"field", "aggregation"}
                or metric["aggregation"] not in ("sum", "avg", "count")):
            raise DatasetSqlError("Provide a field and a supported aggregation for each report metric.")
        references.append(metric["field"])
    for item in filters:
        if not isinstance(item, dict) or set(item) != {"field", "value"}:
            raise DatasetSqlError("Provide a field and value for each report filter.")
        value = item["value"]
        if value is not None and type(value) not in (str, bool):
            raise DatasetSqlError("Provide filter values as strings, booleans or null.")
        if isinstance(value, str) and len(value) > 256:
            raise DatasetSqlError("Filter values must be no longer than 256 characters.")
        references.append(item["field"])
    if any(not isinstance(field, str) or field not in fields for field in references):
        raise DatasetSqlError("The report refers to an unknown Dataset field.")
    names = [*groups, *metrics]
    _check_field_names(dict.fromkeys(names))
    if len(set(names)) != len(names):
        raise DatasetSqlError("Report output names must be unique.")
    return {"group_by": list(groups), "metrics": deepcopy(dict(sorted(metrics.items()))), "filters": deepcopy(filters)}


def resolve_dataset_report(dataset_id, user, published_revision, spec):
    """Resolve the latest published revision for report creation.

    The revision is the public sequence number used by the Dataset resource.
    The caller must validate types and compile under the live catalog and
    execution policy before saving, and reauthorize independently when running.
    """
    if type(dataset_id) is not int or dataset_id < 1:
        raise DatasetSqlError("Select a published Dataset.")
    if type(published_revision) is not int or published_revision < 1:
        raise DatasetSqlError("Select a published Dataset revision.")
    dataset = (models.Dataset.query.filter_by(id=dataset_id, org_id=user.org_id)
               .populate_existing().with_for_update().first())
    if not can_view_dataset(dataset, user) or not user.has_permission("execute_query"):
        raise PermissionError("You do not have permission to create a report from this Dataset.")
    published = (models.DatasetRevision.query.filter_by(dataset_id=dataset.id)
                 .order_by(models.DatasetRevision.revision.desc()).first())
    if published is None or published_revision != published.revision:
        raise DatasetConflict("The published Dataset changed. Reload it before creating the report.")

    base = (models.Query.query.filter_by(id=dataset.base_query_id, org_id=dataset.org_id)
            .populate_existing().with_for_update().first())
    if base is None or base.is_archived or not has_access(base, user, not_view_only):
        raise PermissionError("Full access to the saved base Query is required.")
    source = (models.DataSource.query.filter_by(id=base.data_source_id, org_id=dataset.org_id)
              .populate_existing().first())
    if source is None or source.type != "mysql" or source.paused or source.uses_ssh_tunnel:
        raise DatasetSqlError("Dataset reports currently require an active direct MySQL or MariaDB source.")
    base_revision = models.QueryRevision.query.filter_by(
        id=published.base_revision_id, query_id=base.id, org_id=dataset.org_id
    ).first()
    if base_revision is None or base_revision.snapshot != snapshot_query(base):
        raise DatasetConflict("The saved base Query changed. Revalidate and publish the Dataset again.")

    policy = resolved_policy(source, user) or {"unrestricted": True}
    validated_spec = _validate_report_spec(spec, published.snapshot["fields"])
    return dataset, published, source, base_revision, policy, validated_spec


def attach_dataset_report(query, dataset, published, spec):
    """Persist the association after the caller has validated the report."""
    if (query.id is None or query.id == dataset.base_query_id or query.org_id != dataset.org_id
            or published.dataset_id != dataset.id or published.id is None):
        raise DatasetSqlError("A report requires a distinct saved Query and a matching Dataset revision.")
    validated_spec = _validate_report_spec(spec, published.snapshot["fields"])
    association = DatasetReport(query_id=query.id, dataset_revision_id=published.id, spec=validated_spec)
    models.db.session.add(association)
    return association
