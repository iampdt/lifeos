"""Runs, proposals, executions and the append-only audit log.

Revision ID: 0001
Revises:
Create Date: 2026-11-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "agent_runs",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("workflow", sa.String(64), nullable=False),
        sa.Column("trigger", sa.String(16), nullable=False),
        sa.Column("thread_id", sa.String(128), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("provider", sa.String(32)),
        sa.Column("model", sa.String(64)),
        sa.Column("input_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("llm_latency_ms", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error", pg.JSONB),
        sa.Column("output", pg.JSONB),
    )
    op.create_table(
        "action_proposals",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", pg.UUID(as_uuid=True), sa.ForeignKey("agent_runs.id")),
        sa.Column("kind", sa.String(48), nullable=False),
        sa.Column("target_data_source", sa.String(32), nullable=False),
        sa.Column("target_page_id", sa.String(64)),
        sa.Column("payload", pg.JSONB, nullable=False),
        sa.Column("edited_payload", pg.JSONB),
        sa.Column("before_snapshot", pg.JSONB),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("rule_ids", pg.ARRAY(sa.String(16)), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("importance", sa.String(16), nullable=False, server_default="normal"),
        sa.Column(
            "depends_on", pg.ARRAY(pg.UUID(as_uuid=True)), nullable=False, server_default="{}"
        ),
        sa.Column("idempotency_key", sa.String(64), nullable=False, unique=True),
        sa.Column("target_last_edited_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("decision_note", sa.Text),
        sa.Column("result_page_id", sa.String(64)),
    )
    op.create_index("ix_action_proposals_status", "action_proposals", ["status"])
    op.create_table(
        "action_executions",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "proposal_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("action_proposals.id"),
            nullable=False,
        ),
        sa.Column("attempt", sa.Integer, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("outcome", sa.String(16)),
        sa.Column("error", pg.JSONB),
        sa.UniqueConstraint("proposal_id", "attempt"),
    )
    op.create_index("ix_action_executions_proposal_id", "action_executions", ["proposal_id"])
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("actor", sa.String(48), nullable=False),
        sa.Column("event", sa.String(48), nullable=False),
        sa.Column("entity_type", sa.String(32)),
        sa.Column("entity_id", sa.String(64)),
        sa.Column("details", pg.JSONB, nullable=False, server_default="{}"),
    )
    # Append-only enforced by the database, not by convention.
    op.execute(
        """
        CREATE FUNCTION audit_log_append_only() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'audit_log is append-only';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_log_no_update_delete
        BEFORE UPDATE OR DELETE ON audit_log
        FOR EACH ROW EXECUTE FUNCTION audit_log_append_only();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_log_no_update_delete ON audit_log")
    op.execute("DROP FUNCTION IF EXISTS audit_log_append_only()")
    op.drop_table("audit_log")
    op.drop_table("action_executions")
    op.drop_table("action_proposals")
    op.drop_table("agent_runs")
