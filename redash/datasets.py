# Copyright (c) 2026 Slavcho Spasenoski. Original Bi-Dash contributions.
# SPDX-License-Identifier: BSD-2-Clause
"""Transactional Dataset authoring operations. Callers own commit and rollback."""

from copy import deepcopy

from redash import models
from redash.data_access_runtime import resolved_policy
from redash.data_access_shared import run_shared_query
from redash.dataset_access import can_edit_dataset, can_manage_dataset_access
from redash.dataset_sql import DatasetSqlError, validate_dataset_fields
from redash.query_revisions import snapshot_query


class DatasetConflict(ValueError):
    """The Dataset version changed before the requested mutation."""


def _draft_values(name, fields):
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 255:
        raise DatasetSqlError("Provide a Dataset name of at most 255 characters.")
    validate_dataset_fields(fields)
    return name.strip(), {"fields": deepcopy(fields)}


def create_dataset(user, base_query_id, name, fields):
    if type(base_query_id) is not int or base_query_id < 1:
        raise DatasetSqlError("Select a saved base Query.")
    dataset = models.Dataset(org_id=user.org_id, user_id=user.id, base_query_id=base_query_id)
    if not can_edit_dataset(dataset, user):
        raise PermissionError("Full access to the saved base Query is required.")
    dataset.name, dataset.draft = _draft_values(name, fields)
    models.db.session.add(dataset)
    return dataset


def _lock_dataset(dataset_id, user, version, allowed):
    dataset = (models.Dataset.query.filter_by(id=dataset_id, org_id=user.org_id)
               .populate_existing().with_for_update().first())
    if not allowed(dataset, user):
        raise PermissionError("You do not have permission to change this Dataset.")
    if type(version) is not int or version != dataset.draft_version:
        raise DatasetConflict("The Dataset changed. Reload it before saving.")
    return dataset


def save_dataset_draft(dataset_id, user, version, name, fields):
    dataset = _lock_dataset(dataset_id, user, version, can_edit_dataset)
    name, draft = _draft_values(name, fields)
    if (name, draft) != (dataset.name, dataset.draft):
        dataset.name, dataset.draft = name, draft
        dataset.draft_version += 1
    return dataset


def authoring_source(dataset, user, *, lock=False):
    query = models.Query.query.filter_by(id=dataset.base_query_id, org_id=user.org_id).populate_existing()
    base = (query.with_for_update() if lock else query).first()
    if base is None or not can_edit_dataset(dataset, user):
        raise PermissionError("Full access to the saved base Query is required.")
    from redash.dataset_report_runtime import is_dataset_report
    if is_dataset_report(base):
        raise DatasetSqlError("Choose a source Query without a Dataset dependency.")
    source = (models.DataSource.query.filter_by(id=base.data_source_id, org_id=user.org_id)
              .populate_existing().first())
    if source is None or source.type != "mysql" or source.paused or source.uses_ssh_tunnel:
        raise DatasetSqlError("Dataset preview currently requires an active direct MySQL or MariaDB source.")
    if not user.has_permission("execute_query"):
        raise PermissionError("Query execution is unavailable for this user.")
    revision = (models.QueryRevision.query.filter_by(query_id=base.id, org_id=base.org_id)
                .order_by(models.QueryRevision.revision.desc()).first())
    if revision is None or revision.snapshot != snapshot_query(base):
        raise DatasetConflict("The saved base Query has no matching revision.")

    return source, revision, resolved_policy(source, user) or {"unrestricted": True}


def _preview_locked_draft(dataset, user):
    source, revision, policy = authoring_source(dataset, user, lock=True)
    data, error = run_shared_query(
        source, policy, revision.snapshot["query"], dataset_preview_fields=dataset.draft["fields"]
    )
    if error or data is None:
        raise DatasetSqlError("Dataset preview failed.")
    return revision, data


def preview_dataset_draft(dataset_id, user, version):
    dataset = _lock_dataset(dataset_id, user, version, can_edit_dataset)
    _, data = _preview_locked_draft(dataset, user)
    return data


def publish_dataset(dataset_id, user, version):
    dataset = _lock_dataset(dataset_id, user, version, can_edit_dataset)
    base_revision, _ = _preview_locked_draft(dataset, user)
    published = (models.DatasetRevision.query.filter_by(dataset_id=dataset.id)
                 .order_by(models.DatasetRevision.revision.desc()).first())
    snapshot = {"name": dataset.name, **deepcopy(dataset.draft)}
    if published and published.base_revision_id == base_revision.id and published.snapshot == snapshot:
        return published
    revision = models.DatasetRevision(
        dataset_id=dataset.id,
        revision=published.revision + 1 if published else 1,
        base_revision_id=base_revision.id,
        actor_user_id=user.id,
        snapshot=snapshot,
    )
    models.db.session.add(revision)
    if published:
        _rebind_dependent_reports(dataset, user, base_revision, published, revision)
    dataset.draft_version += 1
    models.db.session.add(models.Event(
        org_id=user.org_id, user_id=user.id, object_type="dataset", object_id=str(dataset.id),
        action="publish", additional_properties={"version": revision.revision},
    ))
    return revision


def _rebind_dependent_reports(dataset, user, base_revision, published, revision):
    dependents = (models.db.session.query(models.DatasetReport, models.Query)
                  .join(models.Query, models.Query.id == models.DatasetReport.query_id)
                  .filter(models.DatasetReport.dataset_revision_id == published.id, models.Query.is_archived.is_(False),
                          models.Query.org_id == dataset.org_id)
                  .order_by(models.Query.id).with_for_update(of=models.Query).all())
    if not dependents:
        return
    source, _, policy = authoring_source(dataset, user, lock=True)
    for link, query in dependents:
        try:
            prepared, error = run_shared_query(
                source, policy, base_revision.snapshot["query"],
                dataset_report=(revision.snapshot["fields"], link.spec, None),
            )
        except ValueError as failure:
            raise DatasetConflict(f"Dataset changes would break report {query.id}.") from failure
        if error or not prepared:
            raise DatasetConflict(f"Dataset changes would break report {query.id}.")
        query.query_text = prepared["sql"]
        query.data_source = source
        query.latest_query_data_id = None
        query.version += 1
        query.last_modified_by = user
        query.changed_by = user
        link.dataset_revision_id = revision.id


def _grant_ids(values):
    if (not isinstance(values, list) or len(values) > 1000
            or any(type(value) is not int or value < 1 for value in values)
            or len(set(values)) != len(values)):
        raise DatasetSqlError("Provide up to 1000 unique positive grant IDs per recipient type.")
    return set(values)


def set_dataset_grants(dataset_id, user, version, user_ids, group_ids):
    dataset = _lock_dataset(dataset_id, user, version, can_manage_dataset_access)
    user_ids, group_ids = _grant_ids(user_ids), _grant_ids(group_ids)
    if (user_ids or group_ids) and not models.DatasetRevision.query.filter_by(dataset_id=dataset.id).first():
        raise DatasetSqlError("Publish the Dataset before sharing it.")
    users = models.User.query.filter_by(org_id=user.org_id).filter(models.User.id.in_(user_ids)).all()
    groups = models.Group.query.filter_by(org_id=user.org_id).filter(models.Group.id.in_(group_ids)).all()
    if user_ids != {candidate.id for candidate in users
                    if candidate.role == "editor" and not candidate.is_disabled and candidate.id != dataset.user_id}:
        raise DatasetSqlError("Select active Editors in this organization.")
    if group_ids != {group.id for group in groups}:
        raise DatasetSqlError("Select groups in this organization.")

    changed = False
    for model, field, requested in ((models.DatasetReadGrant, "user_id", user_ids),
                                    (models.DatasetGroupReadGrant, "group_id", group_ids)):
        existing = {getattr(grant, field): grant for grant in model.query.filter_by(dataset_id=dataset.id)}
        for recipient in existing.keys() - requested:
            models.db.session.delete(existing[recipient])
            changed = True
        for recipient in requested - existing.keys():
            models.db.session.add(model(dataset_id=dataset.id, **{field: recipient}))
            changed = True
    if changed:
        dataset.draft_version += 1
        models.db.session.add(models.Event(
            org_id=user.org_id, user_id=user.id, object_type="dataset", object_id=str(dataset.id),
            action="edit_access",
        ))
    return dataset
