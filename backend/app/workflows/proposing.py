"""Collect a workflow's proposals, then store them in one transaction.

Phase 1 (`add`, `set_due`, `ensure_period`) does the Notion reads: period lookups and the
before-snapshots used for stale detection. Phase 2 (`commit`) only writes to Postgres, so no
transaction stays open across a Notion call. A `task.set_due` whose Week or Month page is
missing gets a `week.create` / `month.create` proposal first and depends on it (R7).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Literal

from app.execution.actions import ActionKind, Payload, SetDue
from app.execution.executor import take_snapshot
from app.services.periods import PeriodResolver, PeriodTarget
from app.workflows.deps import WorkflowDeps


@dataclass
class _Draft:
    kind: ActionKind
    reason: str
    rule_ids: tuple[str, ...]
    payload: Payload | None = None
    snapshot: dict[str, Any] | None = None
    last_edited: datetime | None = None
    creates_period: tuple[str, str] | None = None  # key of the period this draft creates
    title: str | None = None
    move: tuple[str, date, str, PeriodTarget, PeriodTarget] | None = None  # set_due inputs


class ProposalBuilder:
    def __init__(self, deps: WorkflowDeps, resolver: PeriodResolver) -> None:
        self.deps = deps
        self.resolver = resolver
        self._drafts: list[_Draft] = []
        self._period_keys: set[tuple[str, str]] = set()

    def __len__(self) -> int:
        return len(self._drafts)

    async def add(
        self,
        kind: ActionKind,
        payload: Payload,
        *,
        reason: str,
        rule_ids: tuple[str, ...] = (),
        title: str | None = None,
    ) -> None:
        snapshot, edited = await take_snapshot(self.deps.notion, self.deps.specs, kind, payload)
        self._drafts.append(_Draft(kind, reason, rule_ids, payload, snapshot, edited, title=title))

    async def ensure_period(self, target: PeriodTarget, reason: str) -> None:
        if target.create is None or target.key in self._period_keys:
            return
        kind = ActionKind.WEEK_CREATE if target.kind == "week" else ActionKind.MONTH_CREATE
        self._period_keys.add(target.key)
        self._drafts.append(_Draft(kind, reason, ("R7",), target.create, creates_period=target.key))

    async def set_due(
        self,
        page_id: str,
        new_due: date,
        decision: Literal["carry_forward", "reschedule"],
        *,
        reason: str,
        rule_ids: tuple[str, ...],
        title: str | None = None,
    ) -> None:
        week = await self.resolver.resolve("week", new_due)
        month = await self.resolver.resolve("month", new_due)
        await self.ensure_period(week, f"Weekly Progress page for {new_due:%d %b} (R7).")
        await self.ensure_period(month, f"Monthly Progress page for {new_due:%b %Y} (R7).")
        placeholder = _set_due(page_id, new_due, decision, week, month, {})
        snapshot, edited = await take_snapshot(
            self.deps.notion, self.deps.specs, ActionKind.TASK_SET_DUE, placeholder
        )
        self._drafts.append(
            _Draft(
                ActionKind.TASK_SET_DUE, reason, tuple(sorted({*rule_ids, "R7"})),
                snapshot=snapshot, last_edited=edited,
                move=(page_id, new_due, decision, week, month), title=title,
            )
        )  # fmt: skip

    async def commit(
        self, *, run_id: uuid.UUID | None, ttl: timedelta, actor: str
    ) -> list[uuid.UUID]:
        ids: list[uuid.UUID] = []
        created: dict[tuple[str, str], uuid.UUID] = {}
        async with self.deps.session_factory() as session, session.begin():
            service = self.deps.proposals(session)
            for draft in self._drafts:
                payload = draft.payload
                if draft.move:
                    payload = _set_due(*draft.move, created)
                assert payload is not None
                proposal, _ = await service.propose(
                    draft.kind,
                    payload,
                    reason=draft.reason,
                    rule_ids=draft.rule_ids,
                    run_id=run_id,
                    before_snapshot=draft.snapshot,
                    target_last_edited_at=draft.last_edited,
                    ttl=ttl,
                    actor=actor,
                    target_title=draft.title,
                )
                if draft.creates_period:
                    created[draft.creates_period] = proposal.id
                if proposal.id not in ids:
                    ids.append(proposal.id)
        return ids


def _set_due(
    page_id: str,
    new_due: date,
    decision: str,
    week: PeriodTarget,
    month: PeriodTarget,
    created: dict[tuple[str, str], uuid.UUID],
) -> SetDue:
    """Link to the existing page, or to the proposal that creates it. Before commit, a random
    UUID stands in for that proposal (only the snapshot is computed from it)."""
    links: dict[str, Any] = {}
    for name, target in (("week", week), ("month", month)):
        if target.page_id:
            links[f"{name}_page_id"] = target.page_id
        else:
            links[f"{name}_proposal_id"] = created.get(target.key) or uuid.uuid4()
    return SetDue(page_id=page_id, new_due=new_due, decision=decision, **links)
