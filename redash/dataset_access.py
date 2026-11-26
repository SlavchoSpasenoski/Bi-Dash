# Copyright (c) 2026 Slavcho Spasenoski. Original Bi-Dash contributions.
# SPDX-License-Identifier: BSD-2-Clause
"""Dataset definition permissions. Report reads use dashboard authorization."""

from redash import models
from redash.permissions import has_access, not_view_only, view_only


def _active_author(dataset, user):
    return (dataset is not None and not dataset.archived and user is not None
            and not user.is_api_user() and not user.is_disabled
            and user.org_id == dataset.org_id and user.role in {"admin", "editor"})


def _has_base_access(dataset, user, level):
    base = models.Query.query.filter_by(id=dataset.base_query_id, org_id=dataset.org_id).first()
    return bool(base and not base.is_archived and base.data_source is not None
                and base.data_source.org_id == dataset.org_id and has_access(base, user, level))


def can_manage_dataset_access(dataset, user):
    return (_active_author(dataset, user)
            and (user.role == "admin" or dataset.user_id == user.id)
            and _has_base_access(dataset, user, view_only))


def can_edit_dataset(dataset, user):
    return (_active_author(dataset, user)
            and (user.role == "admin" or dataset.user_id == user.id)
            and _has_base_access(dataset, user, not_view_only))


def can_view_dataset(dataset, user):
    if not _active_author(dataset, user) or not _has_base_access(dataset, user, view_only):
        return False
    if user.role == "admin" or dataset.user_id == user.id:
        return True
    if not models.DatasetRevision.query.filter_by(dataset_id=dataset.id).first():
        return False
    return (
        models.DatasetReadGrant.query.filter_by(dataset_id=dataset.id, user_id=user.id).first() is not None
        or models.DatasetGroupReadGrant.query.join(models.Group).filter(
            models.DatasetGroupReadGrant.dataset_id == dataset.id,
            models.Group.org_id == dataset.org_id,
            models.Group.id.in_(user.group_ids or []),
        ).first() is not None
    )
