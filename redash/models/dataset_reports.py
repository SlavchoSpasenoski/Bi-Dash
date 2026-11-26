# Copyright (c) 2026 Slavcho Spasenoski. Original Bi-Dash contributions.
# SPDX-License-Identifier: BSD-2-Clause
"""A saved Query's structured Dataset report definition."""

from sqlalchemy.dialects.postgresql import JSONB

from .base import Column, db, key_type


class DatasetReport(db.Model):
    __tablename__ = "dataset_reports"

    query_id = Column(key_type("Query"), db.ForeignKey("queries.id", ondelete="CASCADE"), primary_key=True)
    dataset_revision_id = Column(
        key_type("DatasetRevision"), db.ForeignKey("dataset_revisions.id"), index=True
    )
    spec = Column(JSONB)
