"""Scheduled jobs: at most one run per job per local day.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "cron_runs",
        sa.Column("job", sa.String(48), primary_key=True),
        sa.Column("local_date", sa.Date, primary_key=True),
        sa.Column("run_id", pg.UUID(as_uuid=True), sa.ForeignKey("agent_runs.id")),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("result", pg.JSONB),
    )


def downgrade() -> None:
    op.drop_table("cron_runs")
