"""Dynamic dashboard view grants for organization groups."""
from alembic import op
import sqlalchemy as sa

revision = "e92c71b54a08"
down_revision = "d83b6a21f409"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("dashboard_group_read_grants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("dashboard_id", sa.Integer(), sa.ForeignKey("dashboards.id", ondelete="CASCADE"), nullable=False),
        sa.Column("group_id", sa.Integer(), sa.ForeignKey("groups.id", ondelete="CASCADE"), nullable=False),
        sa.Column("grantor_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("dashboard_id", "group_id", name="uq_dashboard_group_read_grant"))
    op.create_index("ix_dashboard_group_read_grants_dashboard_id", "dashboard_group_read_grants", ["dashboard_id"])
    op.create_index("ix_dashboard_group_read_grants_group_id", "dashboard_group_read_grants", ["group_id"])


def downgrade():
    op.drop_table("dashboard_group_read_grants")
