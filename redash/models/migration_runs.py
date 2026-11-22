"""Successful logical import receipts; no package contents or credentials."""
from sqlalchemy import UniqueConstraint
from .base import Column, db, key_type, primary_key


class MigrationRun(db.Model):
    __tablename__ = "migration_runs"
    id = primary_key("MigrationRun")
    org_id = Column(key_type("Organization"), db.ForeignKey("organizations.id"), index=True)
    actor_user_id = Column(key_type("User"), db.ForeignKey("users.id"))
    package_id = Column(db.String(36))
    created_at = Column(db.DateTime(timezone=True), server_default=db.func.now())
    __table_args__ = (UniqueConstraint("org_id", "package_id", name="uq_migration_run_package"),)
