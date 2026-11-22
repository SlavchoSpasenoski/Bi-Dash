"""Immutable, allowlisted snapshots of saved query definitions."""
from sqlalchemy import CheckConstraint, UniqueConstraint, event
from sqlalchemy.dialects.postgresql import JSONB

from .base import Column, db, key_type, primary_key
from .mixins import BelongsToOrgMixin


class QueryRevision(BelongsToOrgMixin, db.Model):
    id = primary_key("QueryRevision")
    query_id = Column(key_type("Query"), db.ForeignKey("queries.id", ondelete="CASCADE"), index=True)
    org_id = Column(key_type("Organization"), db.ForeignKey("organizations.id"), index=True)
    revision = Column(db.Integer)
    actor_user_id = Column(key_type("User"), db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    actor_user = db.relationship("User", foreign_keys=[actor_user_id])
    created_at = Column(db.DateTime(timezone=True), server_default=db.func.now())
    kind = Column(db.String(16))
    snapshot = Column(JSONB)
    changed_fields = Column(JSONB)
    __tablename__ = "query_revisions"
    __table_args__ = (
        UniqueConstraint("query_id", "revision", name="uq_query_revision_sequence"),
        CheckConstraint("revision > 0", name="ck_query_revision_positive"),
        CheckConstraint("kind IN ('created', 'baseline', 'update')", name="ck_query_revision_kind"),
    )


@event.listens_for(QueryRevision, "before_update")
def deny_revision_update(mapper, connection, target):
    raise ValueError("Saved query revisions are immutable.")


@event.listens_for(QueryRevision, "before_delete")
def deny_revision_delete(mapper, connection, target):
    raise ValueError("Saved query revisions are immutable.")
