# Copyright (c) 2026 Slavcho Spasenoski. Original Bi-Dash contributions.
# SPDX-License-Identifier: BSD-2-Clause
"""Dataset drafts, published snapshots and explicit definition-read grants."""

from sqlalchemy import CheckConstraint, UniqueConstraint, event
from sqlalchemy.dialects.postgresql import JSONB

from .base import Column, db, key_type, primary_key
from .mixins import BelongsToOrgMixin


class Dataset(BelongsToOrgMixin, db.Model):
    __tablename__ = "datasets"
    __table_args__ = (
        CheckConstraint("draft_version > 0", name="ck_dataset_draft_version"),
    )

    id = primary_key("Dataset")
    org_id = Column(key_type("Organization"), db.ForeignKey("organizations.id"), index=True)
    user_id = Column(key_type("User"), db.ForeignKey("users.id"))
    base_query_id = Column(key_type("Query"), db.ForeignKey("queries.id"), index=True)
    name = Column(db.String(255))
    draft = Column(JSONB)
    draft_version = Column(db.Integer, default=1, server_default="1")
    archived = Column(db.Boolean, default=False, server_default=db.false())


class DatasetRevision(db.Model):
    __tablename__ = "dataset_revisions"
    __table_args__ = (
        UniqueConstraint("dataset_id", "revision", name="uq_dataset_revision_sequence"),
        CheckConstraint("revision > 0", name="ck_dataset_revision_positive"),
    )

    id = primary_key("DatasetRevision")
    dataset_id = Column(key_type("Dataset"), db.ForeignKey("datasets.id", ondelete="CASCADE"))
    base_revision_id = Column(key_type("QueryRevision"), db.ForeignKey("query_revisions.id"))
    actor_user_id = Column(key_type("User"), db.ForeignKey("users.id"))
    revision = Column(db.Integer)
    snapshot = Column(JSONB)
    created_at = Column(db.DateTime(timezone=True), server_default=db.func.now())


class DatasetReadGrant(db.Model):
    __tablename__ = "dataset_read_grants"

    dataset_id = Column(key_type("Dataset"), db.ForeignKey("datasets.id", ondelete="CASCADE"), primary_key=True)
    user_id = Column(key_type("User"), db.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)


class DatasetGroupReadGrant(db.Model):
    __tablename__ = "dataset_group_read_grants"

    dataset_id = Column(key_type("Dataset"), db.ForeignKey("datasets.id", ondelete="CASCADE"), primary_key=True)
    group_id = Column(key_type("Group"), db.ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True)


@event.listens_for(DatasetRevision, "before_update")
@event.listens_for(DatasetRevision, "before_delete")
def deny_revision_change(mapper, connection, target):
    raise ValueError("Published Dataset revisions are immutable.")
