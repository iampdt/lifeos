"""Notion connections: an account's OAuth tokens (encrypted) and workspace

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notion_connections",
        sa.Column("user_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("workspace_id", sa.String(64), nullable=False),
        sa.Column("workspace_name", sa.String(200)),
        sa.Column("workspace_icon", sa.String(500)),
        sa.Column("bot_id", sa.String(64), nullable=False),
        sa.Column("access_token", sa.Text, nullable=False),
        sa.Column("refresh_token", sa.Text),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("duplicated_template_id", sa.String(64)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column(
            "connected_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("refreshed_at", sa.DateTime(timezone=True)),
    )
    # Notion's OAuth has no PKCE verifier or nonce.
    op.alter_column("oauth_states", "code_verifier", existing_type=sa.String(128), nullable=True)
    op.alter_column("oauth_states", "nonce", existing_type=sa.String(64), nullable=True)


def downgrade() -> None:
    op.execute("DELETE FROM oauth_states WHERE code_verifier IS NULL OR nonce IS NULL")
    op.alter_column("oauth_states", "nonce", existing_type=sa.String(64), nullable=False)
    op.alter_column("oauth_states", "code_verifier", existing_type=sa.String(128), nullable=False)
    op.drop_table("notion_connections")
