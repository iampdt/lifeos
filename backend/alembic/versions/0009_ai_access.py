"""AI access per account: free-tier consent, own Gemini key, daily usage

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _user() -> sa.Column:
    return sa.Column("user_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id"), primary_key=True)


def upgrade() -> None:
    op.create_table(
        "ai_access",
        _user(),
        sa.Column("consented_at", sa.DateTime(timezone=True)),
        sa.Column("consent_version", sa.String(16)),
        sa.Column("key", sa.Text),
        sa.Column("key_hint", sa.String(8)),
        sa.Column("key_status", sa.String(16)),
        sa.Column("key_added_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "ai_usage",
        _user(),
        sa.Column("day", sa.Date, primary_key=True),
        sa.Column("service_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("own_tokens", sa.Integer, nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_table("ai_usage")
    op.drop_table("ai_access")
