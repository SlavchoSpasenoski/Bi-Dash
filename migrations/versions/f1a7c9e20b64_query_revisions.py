"""Immutable query definition revisions with an explicit activation baseline."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "f1a7c9e20b64"
down_revision = "e92c71b54a08"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("query_revisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("query_id", sa.Integer(), sa.ForeignKey("queries.id", ondelete="CASCADE"), nullable=False),
        sa.Column("org_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("changed_fields", postgresql.JSONB(), nullable=False),
        sa.UniqueConstraint("query_id", "revision", name="uq_query_revision_sequence"),
        sa.CheckConstraint("revision > 0", name="ck_query_revision_positive"),
        sa.CheckConstraint("kind IN ('created', 'baseline', 'update')", name="ck_query_revision_kind"))
    op.create_index("ix_query_revisions_query_id", "query_revisions", ["query_id"])
    op.create_index("ix_query_revisions_org_id", "query_revisions", ["org_id"])

    # Existing Change rows do not prove complete historical saves. Record only
    # the current definition, clearly labelled baseline at activation time.
    from redash.query_revisions import allowed_snapshot, SNAPSHOT_FIELDS
    connection = op.get_bind()
    source = sa.table("queries", sa.column("id"), sa.column("org_id"), sa.column("user_id"),
        sa.column("last_modified_by_id"), sa.column("name"), sa.column("description"),
        sa.column("query"), sa.column("data_source_id"), sa.column("options", postgresql.JSONB()))
    target = sa.table("query_revisions", sa.column("query_id", sa.Integer()), sa.column("org_id", sa.Integer()),
        sa.column("revision", sa.Integer()), sa.column("actor_user_id", sa.Integer()),
        sa.column("kind", sa.String()), sa.column("snapshot", postgresql.JSONB()), sa.column("changed_fields", postgresql.JSONB()))
    last_id = None
    while True:
        statement = sa.select(source).order_by(source.c.id).limit(500)
        if last_id is not None:
            statement = statement.where(source.c.id > last_id)
        rows = connection.execute(statement).mappings().all()
        if not rows:
            break
        values = []
        for row in rows:
            values.append({"query_id": row["id"], "org_id": row["org_id"], "revision": 1,
                "actor_user_id": row["last_modified_by_id"] or row["user_id"], "kind": "baseline",
                "snapshot": allowed_snapshot(row), "changed_fields": list(SNAPSHOT_FIELDS)})
        connection.execute(target.insert(), values)
        last_id = rows[-1]["id"]


def downgrade():
    op.drop_table("query_revisions")
