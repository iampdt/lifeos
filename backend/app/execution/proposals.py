"""Proposal lifecycle: propose → (approve | reject | expire), with idempotency (design §9.1, §9.4).

pending ──approve──► approved ──(executor)──► executing ──► executed | failed | stale
   ├──reject──► rejected
   └──TTL────► expired
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ActionProposal
from app.execution.actions import (
    DATA_SOURCE,
    PAYLOADS,
    ActionKind,
    Payload,
    dependencies,
    importance,
    parse_payload,
    target_page_id,
)
from app.execution.audit import audit
from app.permissions.policy import DEFAULT_MODES, Mode
from app.services.calendar import DEFAULT_TZ


class ProposalError(Exception):
    pass


def utcnow() -> datetime:
    return datetime.now(UTC)


def idempotency_key(kind: ActionKind | str, payload: Payload, period: date) -> str:
    """Same kind + same payload on the same local day = the same proposal.

    A workflow that runs twice (a retried cron job, a refreshed page) therefore can't create
    duplicate proposals, while the same suggestion on a later day is a new one."""
    canonical = json.dumps(
        {
            "kind": str(kind),
            "payload": payload.model_dump(mode="json"),
            "period": period.isoformat(),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


class ProposalService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        modes: dict[ActionKind, Mode] | None = None,
        clock: Callable[[], datetime] = utcnow,
        tz: ZoneInfo = DEFAULT_TZ,
    ) -> None:
        self.session = session
        self.modes = modes or DEFAULT_MODES
        self.clock = clock
        self.tz = tz

    async def propose(
        self,
        kind: ActionKind,
        payload: Payload,
        *,
        reason: str,
        rule_ids: Sequence[str] = (),
        run_id: uuid.UUID | None = None,
        depends_on: Sequence[uuid.UUID] = (),
        before_snapshot: dict[str, Any] | None = None,
        target_last_edited_at: datetime | None = None,
        ttl: timedelta = timedelta(hours=24),
        actor: str = "system",
    ) -> tuple[ActionProposal, bool]:
        """Create a pending proposal. Returns (proposal, created); created is False when an
        identical proposal already exists for today."""
        kind = ActionKind(kind)
        if self.modes.get(kind, Mode.APPROVE) == Mode.OFF:
            raise ProposalError(f"{kind} is switched off")
        if not isinstance(payload, PAYLOADS[kind]):
            raise ProposalError(f"{kind} needs a {PAYLOADS[kind].__name__} payload")
        now = self.clock()
        key = idempotency_key(kind, payload, now.astimezone(self.tz).date())
        all_deps = sorted(set(depends_on) | set(dependencies(payload)))
        proposal_id = uuid.uuid4()
        statement = (
            insert(ActionProposal)
            .values(
                id=proposal_id,
                run_id=run_id,
                kind=str(kind),
                target_data_source=DATA_SOURCE[kind],
                target_page_id=target_page_id(payload),
                payload=payload.model_dump(mode="json"),
                before_snapshot=before_snapshot,
                reason=reason,
                rule_ids=list(rule_ids),
                status="pending",
                importance=importance(kind, payload),
                depends_on=all_deps,
                idempotency_key=key,
                target_last_edited_at=target_last_edited_at,
                expires_at=now + ttl,
            )
            .on_conflict_do_nothing(index_elements=["idempotency_key"])
            .returning(ActionProposal.id)
        )
        inserted = (await self.session.execute(statement)).scalar_one_or_none()
        if inserted is None:
            existing = await self.session.scalar(
                select(ActionProposal).where(ActionProposal.idempotency_key == key)
            )
            assert existing is not None
            return existing, False
        audit(
            self.session,
            actor=actor,
            event="proposal.created",
            entity_type="proposal",
            entity_id=str(proposal_id),
            details={"kind": str(kind), "target": target_page_id(payload), "rules": list(rule_ids)},
        )
        proposal = await self.session.get(ActionProposal, proposal_id)
        assert proposal is not None
        return proposal, True

    async def _load_for_decision(self, proposal_id: uuid.UUID) -> ActionProposal:
        proposal = await self.session.scalar(
            select(ActionProposal).where(ActionProposal.id == proposal_id).with_for_update()
        )
        if proposal is None:
            raise ProposalError(f"proposal {proposal_id} not found")
        if proposal.status != "pending":
            raise ProposalError(f"proposal is {proposal.status}, not pending")
        if proposal.expires_at <= self.clock():
            # `expire_due` records the status change; a decision on it is simply refused.
            raise ProposalError("proposal has expired")
        return proposal

    async def approve(
        self,
        proposal_id: uuid.UUID,
        *,
        edits: dict[str, Any] | None = None,
        note: str | None = None,
        actor: str = "owner",
    ) -> ActionProposal:
        proposal = await self._load_for_decision(proposal_id)
        kind = ActionKind(proposal.kind)
        if self.modes.get(kind, Mode.APPROVE) != Mode.APPROVE:
            raise ProposalError(f"{kind} is in {self.modes[kind]} mode and can't be executed")
        if edits:
            merged = {**proposal.payload, **edits}
            try:
                edited = parse_payload(kind, merged)
            except ValueError as exc:
                raise ProposalError(f"edits are invalid: {exc}") from exc
            if target_page_id(edited) != proposal.target_page_id:
                raise ProposalError("edits can't change which page the action targets")
            proposal.edited_payload = edited.model_dump(mode="json")
        proposal.status = "approved"
        proposal.decided_at = self.clock()
        proposal.decision_note = note
        audit(
            self.session,
            actor=actor,
            event="proposal.approved",
            entity_type="proposal",
            entity_id=str(proposal.id),
            details={"edited": bool(edits), "fields_edited": sorted(edits or {})},
        )
        return proposal

    async def reject(
        self, proposal_id: uuid.UUID, *, note: str | None = None, actor: str = "owner"
    ) -> ActionProposal:
        proposal = await self._load_for_decision(proposal_id)
        proposal.status = "rejected"
        proposal.decided_at = self.clock()
        proposal.decision_note = note
        audit(self.session, actor=actor, event="proposal.rejected",
              entity_type="proposal", entity_id=str(proposal.id))  # fmt: skip
        return proposal

    async def retry(self, proposal_id: uuid.UUID, *, actor: str = "owner") -> ActionProposal:
        """Put a failed proposal back in the queue (e.g. after Notion was unavailable)."""
        proposal = await self.session.scalar(
            select(ActionProposal).where(ActionProposal.id == proposal_id).with_for_update()
        )
        if proposal is None or proposal.status != "failed":
            raise ProposalError("only failed proposals can be retried")
        proposal.status = "approved"
        audit(self.session, actor=actor, event="proposal.retried",
              entity_type="proposal", entity_id=str(proposal.id))  # fmt: skip
        return proposal

    async def expire_due(self) -> int:
        result = await self.session.execute(
            update(ActionProposal)
            .where(ActionProposal.status == "pending", ActionProposal.expires_at <= self.clock())
            .values(status="expired")
            .returning(ActionProposal.id)
        )
        expired = result.scalars().all()
        for proposal_id in expired:
            audit(self.session, actor="system", event="proposal.expired",
                  entity_type="proposal", entity_id=str(proposal_id))  # fmt: skip
        return len(expired)

    async def pending(self) -> list[ActionProposal]:
        result = await self.session.scalars(
            select(ActionProposal)
            .where(ActionProposal.status == "pending")
            .order_by(ActionProposal.created_at)
        )
        return list(result)
