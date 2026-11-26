"""Associate Dataset report definitions with saved Queries."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "6d42ac190b37"
down_revision = "8e21d4ab67c0"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "dataset_reports",
        sa.Column("query_id", sa.Integer, sa.ForeignKey("queries.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("dataset_revision_id", sa.Integer, sa.ForeignKey("dataset_revisions.id"), nullable=False),
        sa.Column("spec", JSONB, nullable=False),
    )
    op.create_index("ix_dataset_reports_dataset_revision_id", "dataset_reports", ["dataset_revision_id"])


def downgrade():
    op.drop_table("dataset_reports")
