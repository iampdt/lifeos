"""Every run, proposal, audit entry and scheduled-job record belongs to an account

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-02

Existing rows (from the single-owner era) go to the owner: the earliest account. The upgrade
refuses to continue when there are runs or proposals but no account to own them.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OWNER = "(SELECT id FROM users WHERE is_owner)"
OWNED = ("agent_runs", "action_proposals", "cron_runs")


def _user_column(nullable: bool = True) -> sa.Column:
    return sa.Column("user_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=nullable)


def upgrade() -> None:
    op.add_column(
        "users", sa.Column("is_owner", sa.Boolean, nullable=False, server_default=sa.false())
    )
    op.execute(
        "UPDATE users SET is_owner = true"
        " WHERE id = (SELECT id FROM users ORDER BY created_at, id LIMIT 1)"
    )
    op.create_index(
        "uq_users_one_owner",
        "users",
        ["is_owner"],
        unique=True,
        postgresql_where=sa.text("is_owner"),
    )

    for table in OWNED:
        op.add_column(table, _user_column())
    op.execute(f"UPDATE agent_runs SET user_id = {OWNER}")
    op.execute(
        "UPDATE action_proposals p SET user_id = coalesce("
        f"(SELECT r.user_id FROM agent_runs r WHERE r.id = p.run_id), {OWNER})"
    )
    op.execute(f"UPDATE cron_runs SET user_id = {OWNER}")
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM agent_runs WHERE user_id IS NULL)
               OR EXISTS (SELECT 1 FROM action_proposals WHERE user_id IS NULL)
               OR EXISTS (SELECT 1 FROM cron_runs WHERE user_id IS NULL) THEN
                RAISE EXCEPTION 'runs or proposals exist but no account owns them'
                    USING HINT = 'Create the account first, then upgrade again: INSERT INTO '
                        'users (id, email) VALUES (gen_random_uuid(), ''you@example.com''); '
                        'afterwards, python -m scripts.create_owner EMAIL --reset sets its '
                        'password.';
            END IF;
        END $$;
        """
    )
    for table in OWNED:
        op.alter_column(table, "user_id", nullable=False)

    op.create_index("ix_agent_runs_user_started", "agent_runs", ["user_id", "started_at"])
    op.create_index("ix_action_proposals_user_status", "action_proposals", ["user_id", "status"])
    op.drop_constraint("action_proposals_idempotency_key_key", "action_proposals", type_="unique")
    op.create_unique_constraint(
        "uq_action_proposals_user_key", "action_proposals", ["user_id", "idempotency_key"]
    )
    op.drop_constraint("cron_runs_pkey", "cron_runs", type_="primary")
    op.create_primary_key("cron_runs_pkey", "cron_runs", ["user_id", "job", "local_date"])

    # The audit log: existing entries are the owner's. The append-only trigger is paused for the
    # backfill only.
    op.add_column("audit_log", _user_column())
    op.execute("ALTER TABLE audit_log DISABLE TRIGGER audit_log_no_update_delete")
    op.execute(f"UPDATE audit_log SET user_id = {OWNER}")
    op.execute("ALTER TABLE audit_log ENABLE TRIGGER audit_log_no_update_delete")
    op.create_index("ix_audit_log_user_id", "audit_log", ["user_id"])

    # Still append-only, except that purge_user_audit() may delete one account's entries: the
    # trigger lets a DELETE through only for rows of the account being purged, in that call.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION audit_log_append_only() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE'
               AND OLD.user_id IS NOT NULL
               AND current_setting('lifeos.purging_user', true) = OLD.user_id::text THEN
                RETURN OLD;
            END IF;
            RAISE EXCEPTION 'audit_log is append-only';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE FUNCTION purge_user_audit(target uuid) RETURNS integer AS $$
        DECLARE
            removed integer;
        BEGIN
            PERFORM set_config('lifeos.purging_user', target::text, true);
            DELETE FROM audit_log WHERE user_id = target;
            GET DIAGNOSTICS removed = ROW_COUNT;
            PERFORM set_config('lifeos.purging_user', '', true);
            RETURN removed;
        END;
        $$ LANGUAGE plpgsql;
        """
    )


def downgrade() -> None:
    # The older schema is single-account: this fails if several accounts' rows clash (the same
    # proposal key or scheduled-job day).
    op.execute("DROP FUNCTION IF EXISTS purge_user_audit(uuid)")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION audit_log_append_only() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'audit_log is append-only';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.drop_index("ix_audit_log_user_id", "audit_log")
    op.drop_column("audit_log", "user_id")

    op.drop_constraint("cron_runs_pkey", "cron_runs", type_="primary")
    op.create_primary_key("cron_runs_pkey", "cron_runs", ["job", "local_date"])
    op.drop_constraint("uq_action_proposals_user_key", "action_proposals", type_="unique")
    op.create_unique_constraint(
        "action_proposals_idempotency_key_key", "action_proposals", ["idempotency_key"]
    )
    op.drop_index("ix_action_proposals_user_status", "action_proposals")
    op.drop_index("ix_agent_runs_user_started", "agent_runs")
    for table in OWNED:
        op.drop_column(table, "user_id")
    op.drop_index("uq_users_one_owner", "users")
    op.drop_column("users", "is_owner")
