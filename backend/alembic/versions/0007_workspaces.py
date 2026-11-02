"""Workspaces: an account's module mapping and rule settings

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "workspaces",
        sa.Column("user_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("mapping", pg.JSONB, nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("max_major_per_day", sa.Integer, nullable=False),
        sa.Column("review_weekday", sa.Integer, nullable=False),
        sa.Column("review_time", sa.String(5), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("workspaces")
