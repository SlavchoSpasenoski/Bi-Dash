"""Track completed portable imports without persisting their contents."""
from alembic import op
import sqlalchemy as sa

revision = "b3d9e1a42f86"
down_revision = "f1a7c9e20b64"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("migration_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("org_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("package_id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("org_id", "package_id", name="uq_migration_run_package"))
    op.create_index("ix_migration_runs_org_id", "migration_runs", ["org_id"])


def downgrade():
    op.drop_table("migration_runs")
