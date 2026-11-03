"""Keep the order of a saved workspace mapping (json instead of jsonb)

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-03
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE workspaces ALTER COLUMN mapping TYPE json USING mapping::json")


def downgrade() -> None:
    op.execute("ALTER TABLE workspaces ALTER COLUMN mapping TYPE jsonb USING mapping::jsonb")
