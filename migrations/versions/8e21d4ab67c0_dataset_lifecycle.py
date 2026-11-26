"""Persist Dataset drafts, published revisions and access grants."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "8e21d4ab67c0"
down_revision = "b3d9e1a42f86"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "datasets",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("org_id", sa.Integer, sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("base_query_id", sa.Integer, sa.ForeignKey("queries.id"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("draft", JSONB, nullable=False),
        sa.Column("draft_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("archived", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.CheckConstraint("draft_version > 0", name="ck_dataset_draft_version"),
    )
    op.create_index("ix_datasets_org_id", "datasets", ["org_id"])
    op.create_index("ix_datasets_base_query_id", "datasets", ["base_query_id"])
    op.create_table(
        "dataset_revisions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("dataset_id", sa.Integer, sa.ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("base_revision_id", sa.Integer, sa.ForeignKey("query_revisions.id"), nullable=False),
        sa.Column("actor_user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("snapshot", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("dataset_id", "revision", name="uq_dataset_revision_sequence"),
        sa.CheckConstraint("revision > 0", name="ck_dataset_revision_positive"),
    )
    op.create_table(
        "dataset_read_grants",
        sa.Column("dataset_id", sa.Integer, sa.ForeignKey("datasets.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    )
    op.create_table(
        "dataset_group_read_grants",
        sa.Column("dataset_id", sa.Integer, sa.ForeignKey("datasets.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("group_id", sa.Integer, sa.ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True),
    )


def downgrade():
    op.drop_table("dataset_group_read_grants")
    op.drop_table("dataset_read_grants")
    op.drop_table("dataset_revisions")
    op.drop_table("datasets")
