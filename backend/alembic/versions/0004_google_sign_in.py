"""Google sign-in: account fields and OAuth attempt state.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NOW = sa.text("now()")


def upgrade() -> None:
    op.add_column("users", sa.Column("google_sub", sa.String(64), unique=True))
    op.add_column("users", sa.Column("name", sa.String(200)))
    op.alter_column("users", "password_hash", existing_type=sa.Text, nullable=True)
    op.create_table(
        "oauth_states",
        sa.Column("state", sa.String(64), primary_key=True),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("code_verifier", sa.String(128), nullable=False),
        sa.Column("nonce", sa.String(64), nullable=False),
        sa.Column("user_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("oauth_states")
    op.alter_column("users", "password_hash", existing_type=sa.Text, nullable=False)
    op.drop_column("users", "name")
    op.drop_column("users", "google_sub")
