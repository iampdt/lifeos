"""Workspaces: routine times, automatic runs on or off, and the daily drift check's result

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMNS = ("morning_time", "evening_time", "schedule_enabled", "paused_modules", "drift",
           "checked_at")  # fmt: skip


def upgrade() -> None:
    op.add_column("workspaces", sa.Column("morning_time", sa.String(5), nullable=False,
                                          server_default="07:30"))  # fmt: skip
    op.add_column("workspaces", sa.Column("evening_time", sa.String(5), nullable=False,
                                          server_default="21:00"))  # fmt: skip
    op.add_column("workspaces", sa.Column("schedule_enabled", sa.Boolean, nullable=False,
                                          server_default=sa.true()))  # fmt: skip
    op.add_column("workspaces", sa.Column("paused_modules", pg.JSON, nullable=False,
                                          server_default="[]"))  # fmt: skip
    op.add_column("workspaces", sa.Column("drift", pg.JSON))
    op.add_column("workspaces", sa.Column("checked_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    for column in reversed(COLUMNS):
        op.drop_column("workspaces", column)
