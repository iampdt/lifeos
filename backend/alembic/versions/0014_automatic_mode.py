"""Automatic mode: proposals LifeOS approved itself, and the values that undo them

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "action_proposals",
        sa.Column("auto_applied", sa.Boolean, nullable=False, server_default=sa.false()),
    )
    op.add_column("action_proposals", sa.Column("restore", pg.JSONB))
    op.add_column("action_proposals", sa.Column("undone_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("action_proposals", "undone_at")
    op.drop_column("action_proposals", "restore")
    op.drop_column("action_proposals", "auto_applied")
