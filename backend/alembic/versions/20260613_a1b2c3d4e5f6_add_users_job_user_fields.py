"""add users table and job user fields

Revision ID: a1b2c3d4e5f6
Revises:
Create Date: 2026-06-13 00:00:00.000000

Changes:
  - CREATE TABLE users (id, email, display_name, webex_host_id,
                        confluence_space_key, confluence_parent_page_id,
                        created_at, updated_at)
  - ALTER TABLE jobs ADD COLUMN user_id  (FK → users.id, nullable)
  - ALTER TABLE jobs ADD COLUMN host_email (nullable)

Defensive design: safe to run against both fresh databases (where `jobs` may
not yet exist — `init_db()` / `create_all` will build it with the new columns
already present) and existing deployments (where `jobs` exists without the new
columns and they must be added here).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()

    # ── 1. Create users table (skip if already present) ───────────────────
    if "users" not in existing_tables:
        op.create_table(
            "users",
            sa.Column("id",                        sa.String(36),              nullable=False),
            sa.Column("email",                     sa.String(256),             nullable=False),
            sa.Column("display_name",              sa.String(256),             nullable=True),
            sa.Column("webex_host_id",             sa.String(256),             nullable=True),
            sa.Column("confluence_space_key",      sa.String(64),              nullable=True),
            sa.Column("confluence_parent_page_id", sa.String(64),              nullable=True),
            sa.Column("created_at",                sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at",                sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("email", name="uq_users_email"),
        )
        op.create_index("ix_users_email",         "users", ["email"],         unique=True)
        op.create_index("ix_users_webex_host_id", "users", ["webex_host_id"], unique=False)

    # ── 2. Add user_id / host_email to jobs ───────────────────────────────
    # If jobs doesn't exist yet (fresh database), skip: create_all() will
    # build the table with these columns already in the ORM definition.
    if "jobs" in existing_tables:
        existing_cols = {c["name"] for c in inspector.get_columns("jobs")}
        with op.batch_alter_table("jobs", schema=None) as batch_op:
            if "user_id" not in existing_cols:
                batch_op.add_column(sa.Column("user_id", sa.String(36), nullable=True))
                batch_op.create_index("ix_jobs_user_id", ["user_id"], unique=False)
                batch_op.create_foreign_key(
                    "fk_jobs_user_id", "users", ["user_id"], ["id"],
                )
            if "host_email" not in existing_cols:
                batch_op.add_column(sa.Column("host_email", sa.String(256), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()

    # ── 2. Remove columns from jobs ───────────────────────────────────────
    if "jobs" in existing_tables:
        existing_cols = {c["name"] for c in inspector.get_columns("jobs")}
        with op.batch_alter_table("jobs", schema=None) as batch_op:
            if "user_id" in existing_cols:
                batch_op.drop_constraint("fk_jobs_user_id", type_="foreignkey")
                batch_op.drop_index("ix_jobs_user_id")
                batch_op.drop_column("user_id")
            if "host_email" in existing_cols:
                batch_op.drop_column("host_email")

    # ── 1. Drop users table ───────────────────────────────────────────────
    if "users" in existing_tables:
        op.drop_index("ix_users_webex_host_id", table_name="users")
        op.drop_index("ix_users_email",         table_name="users")
        op.drop_table("users")
