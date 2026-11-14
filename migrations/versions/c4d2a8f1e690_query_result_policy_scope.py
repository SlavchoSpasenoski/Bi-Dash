"""Persist immutable group-policy scope on cached results.

Revision ID: c4d2a8f1e690
Revises: 2f2a8a6f9c21
"""
from alembic import op
import sqlalchemy as sa

revision = "c4d2a8f1e690"
down_revision = "2f2a8a6f9c21"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("query_results", sa.Column("policy_scope", sa.String(length=64), nullable=True))
    op.create_index("ix_query_results_policy_scope", "query_results", ["policy_scope"], unique=False)

def downgrade():
    op.drop_index("ix_query_results_policy_scope", table_name="query_results")
    op.drop_column("query_results", "policy_scope")
