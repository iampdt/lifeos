"""Postgres tables for state LifeOS owns. Productivity records stay in Notion (decision D1)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSON, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (Index("ix_agent_runs_user_started", "user_id", "started_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    workflow: Mapped[str] = mapped_column(String(64))
    trigger: Mapped[str] = mapped_column(String(16))  # manual | cron | chat | test
    thread_id: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(
        String(24)
    )  # running | waiting_approval | completed | failed
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider: Mapped[str | None] = mapped_column(String(32))
    model: Mapped[str | None] = mapped_column(String(64))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    llm_latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class ActionProposal(Base):
    __tablename__ = "action_proposals"
    __table_args__ = (
        # Idempotency is per account: two people can get the same suggestion on the same day.
        UniqueConstraint("user_id", "idempotency_key", name="uq_action_proposals_user_key"),
        Index("ix_action_proposals_user_status", "user_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agent_runs.id"))
    kind: Mapped[str] = mapped_column(String(48))
    target_data_source: Mapped[str] = mapped_column(String(32))
    target_page_id: Mapped[str | None] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    edited_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # Values of the target fields when the proposal was made; used for stale detection.
    before_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    reason: Mapped[str] = mapped_column(Text)
    rule_ids: Mapped[list[str]] = mapped_column(ARRAY(String(16)), default=list)
    status: Mapped[str] = mapped_column(String(16), index=True)
    importance: Mapped[str] = mapped_column(String(16), default="normal")
    depends_on: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)), default=list)
    idempotency_key: Mapped[str] = mapped_column(String(64))
    target_last_edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(Text)
    # Page created or updated by execution (lets dependent proposals use a newly created page).
    result_page_id: Mapped[str | None] = mapped_column(String(64))
    # Title of the target (or of the page to create), so approval screens can name it.
    target_title: Mapped[str | None] = mapped_column(String(200))

    @property
    def effective_payload(self) -> dict[str, Any]:
        return self.edited_payload if self.edited_payload is not None else self.payload


class ActionExecution(Base):
    __tablename__ = "action_executions"
    __table_args__ = (UniqueConstraint("proposal_id", "attempt"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    proposal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("action_proposals.id"), index=True)
    attempt: Mapped[int] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[str | None] = mapped_column(String(16))  # verified | mismatch | stale | failed
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class AuditLog(Base):
    """Append-only. A database trigger rejects UPDATE and DELETE; the one exception is
    `purge_user_audit(user_id)`, which deleting an account calls (migration 0005)."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # The account the entry concerns; null for events without one (e.g. a failed sign-in).
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # user (the account's person; "owner" before migration 0005) | agent:<name> | system | cron
    actor: Mapped[str] = mapped_column(String(48))
    event: Mapped[str] = mapped_column(String(48))
    entity_type: Mapped[str | None] = mapped_column(String(32))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class User(Base):
    """An account. Every run, proposal, audit entry and scheduled-job record belongs to one."""

    __tablename__ = "users"
    __table_args__ = (
        Index("uq_users_one_owner", "is_owner", unique=True, postgresql_where=text("is_owner")),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    # The deployment's owner (at most one): uses the Notion workspace configured in the
    # environment, and is the account behind the bearer token and the scheduled jobs.
    is_owner: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # Google's stable account ID; set on first Google sign-in.
    google_sub: Mapped[str | None] = mapped_column(String(64), unique=True)
    name: Mapped[str | None] = mapped_column(String(200))
    # Only for password sign-in (deployments without Google); null for Google-only accounts.
    password_hash: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CronRun(Base):
    """One row per (account, job, local date): a retried or duplicated cron call can't start a
    second run."""

    __tablename__ = "cron_runs"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    job: Mapped[str] = mapped_column(String(48), primary_key=True)
    local_date: Mapped[date] = mapped_column(Date, primary_key=True)
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agent_runs.id"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class OAuthState(Base):
    """One sign-in or connection attempt: the values the provider's callback must match.
    Single use and short-lived."""

    __tablename__ = "oauth_states"

    state: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider: Mapped[str] = mapped_column(String(16))  # google | notion
    # PKCE verifier and OpenID nonce (Google only).
    code_verifier: Mapped[str | None] = mapped_column(String(128))
    nonce: Mapped[str | None] = mapped_column(String(64))
    # The account a connection attempt belongs to (Notion); its callback must come from it.
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class NotionConnection(Base):
    """An account's connection to its Notion workspace (one per account for now), from Notion's
    OAuth flow. The tokens are encrypted with LIFEOS_ENCRYPTION_KEY (app/core/crypto.py)."""

    __tablename__ = "notion_connections"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(64))
    workspace_name: Mapped[str | None] = mapped_column(String(200))
    workspace_icon: Mapped[str | None] = mapped_column(String(500))  # an emoji or a URL
    bot_id: Mapped[str] = mapped_column(String(64))
    access_token: Mapped[str] = mapped_column(Text)
    refresh_token: Mapped[str | None] = mapped_column(Text)
    # Only when Notion says how long the access token lasts; otherwise a 401 triggers a refresh.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The starter template Notion duplicated during authorisation, if any.
    duplicated_template_id: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))  # active | needs_reconnect
    connected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    refreshed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Workspace(Base):
    """An account's own workspace: which data source and properties each module uses (built
    during onboarding) and the account's parameters for the rules."""

    __tablename__ = "workspaces"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    # Module name → DataSourceSpec as JSON (app/workspaces/store.py). `json`, not `jsonb`, so
    # habits and fields keep the order the account set them up in.
    mapping: Mapped[dict[str, Any]] = mapped_column(JSON)
    timezone: Mapped[str] = mapped_column(String(64))
    max_major_per_day: Mapped[int] = mapped_column(Integer, default=3)  # R1
    # Scheduled routines, local to `timezone`: the weekly review's day (Monday is 0) and times.
    review_weekday: Mapped[int] = mapped_column(Integer, default=6)
    review_time: Mapped[str] = mapped_column(String(5), default="18:00")
    morning_time: Mapped[str] = mapped_column(String(5), default="07:30")
    evening_time: Mapped[str] = mapped_column(String(5), default="21:00")
    schedule_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # The daily drift check: modules whose data source no longer matches the mapping are paused
    # (left out of the workspace) until the mapping is saved again.
    paused_modules: Mapped[list[str]] = mapped_column(JSON, default=list)
    drift: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AIAccess(Base):
    """How an account may use AI (multi-user §AI under free tier): its agreement to the Gemini
    free-tier terms for the service's key, and its own Gemini key (encrypted)."""

    __tablename__ = "ai_access"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    consented_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consent_version: Mapped[str | None] = mapped_column(String(16))
    key: Mapped[str | None] = mapped_column(Text)
    key_hint: Mapped[str | None] = mapped_column(String(8))  # the key's last four characters
    key_status: Mapped[str | None] = mapped_column(String(16))  # active | rejected
    key_added_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AIUsage(Base):
    """Tokens an account used per UTC day, on the service's key and on its own."""

    __tablename__ = "ai_usage"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    service_tokens: Mapped[int] = mapped_column(Integer, default=0)
    own_tokens: Mapped[int] = mapped_column(Integer, default=0)
