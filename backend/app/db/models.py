"""Postgres tables for state LifeOS owns. Productivity records stay in Notion (design D1)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    ARRAY,
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
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

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
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
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    target_last_edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(Text)
    # Page created or updated by execution (lets dependent proposals use a newly created page).
    result_page_id: Mapped[str | None] = mapped_column(String(64))

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
    """Append-only. A database trigger rejects UPDATE and DELETE (see migration 0001)."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    actor: Mapped[str] = mapped_column(String(48))  # owner | agent:<name> | system | cron
    event: Mapped[str] = mapped_column(String(48))
    entity_type: Mapped[str | None] = mapped_column(String(32))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
