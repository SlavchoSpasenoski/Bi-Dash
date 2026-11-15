"""Explicit dashboard reader grants and server-only report provenance.

Revision ID: d83b6a21f409
Revises: c4d2a8f1e690
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "d83b6a21f409"
down_revision = "c4d2a8f1e690"
branch_labels = None
depends_on = None


def upgrade():
    # No existing dashboards are automatically shared or rewritten.
    op.create_table(
        "dashboard_read_grants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("dashboard_id", sa.Integer(), sa.ForeignKey("dashboards.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("grantor_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("dashboard_id", "user_id", name="uq_dashboard_read_grant"),
    )
    op.create_index("ix_dashboard_read_grants_dashboard_id", "dashboard_read_grants", ["dashboard_id"])
    op.create_index("ix_dashboard_read_grants_user_id", "dashboard_read_grants", ["user_id"])
    op.add_column("query_results", sa.Column("report_context", postgresql.JSONB(), nullable=True))


def downgrade():
    op.drop_column("query_results", "report_context")
    op.drop_index("ix_dashboard_read_grants_user_id", table_name="dashboard_read_grants")
    op.drop_index("ix_dashboard_read_grants_dashboard_id", table_name="dashboard_read_grants")
    op.drop_table("dashboard_read_grants")
