"""add publish_failed column to jobs

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-06-15 00:00:00.000000

Changes:
  - ALTER TABLE jobs ADD COLUMN publish_failed BOOLEAN NOT NULL DEFAULT FALSE

Safe to run on existing databases — skips if column already present.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()

    if "jobs" in existing_tables:
        existing_cols = {c["name"] for c in inspector.get_columns("jobs")}
        if "publish_failed" not in existing_cols:
            with op.batch_alter_table("jobs", schema=None) as batch_op:
                batch_op.add_column(
                    sa.Column(
                        "publish_failed",
                        sa.Boolean(),
                        nullable=False,
                        server_default=sa.false(),
                    )
                )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()

    if "jobs" in existing_tables:
        existing_cols = {c["name"] for c in inspector.get_columns("jobs")}
        if "publish_failed" in existing_cols:
            with op.batch_alter_table("jobs", schema=None) as batch_op:
                batch_op.drop_column("publish_failed")
