"""Execute approved proposals against Notion (architecture §3.4, §6).

For each approved proposal, in dependency order:
  1. kill switch and allowlist checks
  2. updates: re-read the page; if it already has the target values → `noop`;
     if it changed since the proposal was made → `stale` (needs re-approval)
  3. creates: find-or-create by natural key, so a retry never duplicates a page
  4. write, then re-read and compare → `verified` or `mismatch`
  5. record the attempt and append to the audit log

Notion I/O happens outside database transactions, so a slow API call never holds a row lock.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import ActionExecution, ActionProposal
from app.execution.actions import (
    ActionKind,
    Payload,
    WritePlan,
    build_plan,
    dependencies,
    expected_fields,
    parse_payload,
    read_fields,
)
from app.execution.audit import audit
from app.notion.codecs import parse_notion_datetime
from app.notion.encoders import encode_properties
from app.notion.errors import NotionError
from app.notion.mapping import DataSourceSpec
from app.notion.queries import QuerySpec
from app.notion.repositories import HabitRepository, PeriodRepository, TaskRepository
from app.permissions.policy import PermissionDenied, check_plan
from app.services.calendar import DEFAULT_TZ

logger = logging.getLogger("lifeos.executor")

TERMINAL_FAILURES = {"rejected", "failed", "stale", "expired"}


class NotionWriter(Protocol):
    async def get_page(self, page_id: str) -> dict[str, Any]: ...
    async def update_page(self, page_id: str, properties: dict[str, Any]) -> dict[str, Any]: ...
    async def create_page(
        self, data_source_id: str, properties: dict[str, Any]
    ) -> dict[str, Any]: ...
    def query_data_source(self, data_source_id: str, **kwargs: Any): ...
    async def get_relation_ids(self, page_id: str, property_id: str) -> list[str]: ...


@dataclass(frozen=True)
class ExecutionResult:
    proposal_id: uuid.UUID
    outcome: str  # verified | noop | mismatch | stale | failed | blocked | skipped
    page_id: str | None = None
    detail: str = ""


class Executor:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        client: NotionWriter,
        specs: dict[str, DataSourceSpec],
        *,
        user_id: uuid.UUID,
        writes_enabled: bool,
        tz: ZoneInfo = DEFAULT_TZ,
        actor: str = "system",
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """Executes one account's approved proposals against that account's workspace."""
        self.session_factory = session_factory
        self.client = client
        self.specs = specs
        self.user_id = user_id
        self.writes_enabled = writes_enabled
        self.tz = tz
        self.actor = actor
        self.clock = clock

    # --- batch ---------------------------------------------------------------------------

    async def execute_approved(
        self, proposal_ids: Sequence[uuid.UUID] | None = None
    ) -> list[ExecutionResult]:
        async with self.session_factory() as session:
            query = select(ActionProposal).where(
                ActionProposal.user_id == self.user_id, ActionProposal.status == "approved"
            )
            if proposal_ids is not None:
                query = query.where(ActionProposal.id.in_(list(proposal_ids)))
            approved = list(await session.scalars(query.order_by(ActionProposal.created_at)))

        if not self.writes_enabled:
            return [
                ExecutionResult(p.id, "blocked", detail="writes are disabled") for p in approved
            ]

        results: list[ExecutionResult] = []
        for proposal in self._dependency_order(approved):
            results.append(await self.execute_one(proposal.id))
        return results

    @staticmethod
    def _dependency_order(proposals: list[ActionProposal]) -> list[ActionProposal]:
        """Dependencies inside the batch run first (stable topological order)."""
        by_id = {p.id: p for p in proposals}
        ordered: list[ActionProposal] = []
        seen: set[uuid.UUID] = set()

        def visit(p: ActionProposal, path: frozenset[uuid.UUID]) -> None:
            if p.id in seen:
                return
            if p.id in path:
                raise ValueError(f"dependency cycle at proposal {p.id}")
            for dep in p.depends_on or []:
                if dep in by_id:
                    visit(by_id[dep], path | {p.id})
            seen.add(p.id)
            ordered.append(p)

        for proposal in proposals:
            visit(proposal, frozenset())
        return ordered

    # --- single proposal -----------------------------------------------------------------

    async def execute_one(self, proposal_id: uuid.UUID) -> ExecutionResult:
        if not self.writes_enabled:
            return ExecutionResult(proposal_id, "blocked", detail="writes are disabled")

        async with self.session_factory() as session, session.begin():
            proposal = await session.scalar(
                select(ActionProposal)
                .where(ActionProposal.id == proposal_id, ActionProposal.user_id == self.user_id)
                .with_for_update()
            )
            if proposal is None or proposal.status != "approved":
                status = proposal.status if proposal else "missing"
                return ExecutionResult(proposal_id, "skipped", detail=f"proposal is {status}")
            if proposal.expires_at <= self.clock():
                # An approval left unexecuted (e.g. while writes were disabled) must not fire
                # days later against pages that may have moved on.
                proposal.status = "expired"
                audit(session, actor=self.actor, event="proposal.expired",
                      user_id=self.user_id, entity_type="proposal", entity_id=str(proposal_id),
                      details={"reason": "approved but not executed before expiry"})  # fmt: skip
                return ExecutionResult(proposal_id, "skipped", detail="approval expired")
            resolved, blocker = await self._resolve_dependencies(session, proposal)
            if blocker == "waiting":
                return ExecutionResult(
                    proposal_id, "skipped", detail="dependencies not executed yet"
                )
            attempt = 1 + (
                await session.scalar(
                    select(func.count()).where(ActionExecution.proposal_id == proposal_id)
                )
                or 0
            )
            execution = ActionExecution(proposal_id=proposal_id, attempt=attempt)
            session.add(execution)
            proposal.status = "executing"
            kind = ActionKind(proposal.kind)
            payload_data = proposal.effective_payload
            snapshot = proposal.before_snapshot
            await session.flush()
            execution_id = execution.id

        page_id: str | None = None
        error: dict[str, Any] | None = None
        try:
            if blocker:
                raise _DependencyFailed(blocker)
            plan = build_plan(kind, parse_payload(kind, payload_data), self.specs, resolved)
            check_plan(plan, self.specs[plan.data_source])
            page_id, outcome, detail = await self._apply(plan, snapshot)
        except PermissionDenied as exc:
            outcome, detail, error = "failed", str(exc), {"type": "permission_denied"}
        except _DependencyFailed as exc:
            outcome, detail, error = "failed", str(exc), {"type": "dependency_failed"}
        except NotionError as exc:
            outcome, detail = "failed", str(exc)
            error = {"type": type(exc).__name__, "code": exc.code, "retryable": exc.retryable,
                     "request_id": exc.request_id}  # fmt: skip
        except Exception as exc:  # recorded, never swallowed silently
            logger.exception("execution of %s crashed", proposal_id)
            outcome, detail, error = "failed", repr(exc), {"type": "internal_error"}

        status = {"verified": "executed", "noop": "executed", "mismatch": "executed",
                  "stale": "stale"}.get(outcome, "failed")  # fmt: skip
        async with self.session_factory() as session, session.begin():
            proposal = await session.get(ActionProposal, proposal_id, with_for_update=True)
            execution = await session.get(ActionExecution, execution_id)
            assert proposal is not None and execution is not None
            proposal.status = status
            proposal.result_page_id = page_id
            execution.outcome = outcome
            execution.finished_at = datetime.now(UTC)
            execution.error = error
            audit(
                session,
                actor=self.actor,
                event=f"execution.{outcome}",
                user_id=self.user_id,
                entity_type="proposal",
                entity_id=str(proposal_id),
                details={
                    "kind": str(kind),
                    "page_id": page_id,
                    "attempt": execution.attempt,
                    "detail": detail[:500],
                },  # fmt: skip
            )
        return ExecutionResult(proposal_id, outcome, page_id, detail)

    async def _resolve_dependencies(
        self, session: AsyncSession, proposal: ActionProposal
    ) -> tuple[dict[uuid.UUID, str], str | None]:
        if not proposal.depends_on:
            return {}, None
        deps = list(
            await session.scalars(
                select(ActionProposal).where(
                    ActionProposal.id.in_(proposal.depends_on),
                    ActionProposal.user_id == self.user_id,
                )
            )
        )
        if len(deps) != len(set(proposal.depends_on)):
            return {}, "a dependency is missing"
        for dep in deps:
            if dep.status in TERMINAL_FAILURES:
                return {}, f"dependency {dep.id} is {dep.status}"
            if dep.status != "executed" or not dep.result_page_id:
                return {}, "waiting"
        return {dep.id: dep.result_page_id for dep in deps}, None

    # --- Notion side ---------------------------------------------------------------------

    async def _apply(
        self, plan: WritePlan, snapshot: dict[str, Any] | None
    ) -> tuple[str, str, str]:
        spec = self.specs[plan.data_source]
        values = dict(plan.values)

        if plan.page_id:
            page = await self.client.get_page(plan.page_id)
            current = read_fields(spec, page, list(values))
            if current == expected_fields(values):
                return plan.page_id, "noop", "already had the target values"
            if snapshot:
                now = {k: current.get(k) for k in snapshot}
                if now != snapshot:
                    changed = sorted(k for k in snapshot if now.get(k) != snapshot[k])
                    return plan.page_id, "stale", f"changed in Notion since proposal: {changed}"
            await self.client.update_page(plan.page_id, encode_properties(spec.properties, values))
            page_id = plan.page_id
        else:
            existing = await self._find_existing(plan)
            if existing and not plan.upsert:
                return existing, "noop", "a matching page already exists"
            if existing:
                values.pop("title", None)  # keep the owner's existing title format
                page = await self.client.get_page(existing)
                if read_fields(spec, page, list(values)) == expected_fields(values):
                    return existing, "noop", "already had the target values"
                await self.client.update_page(existing, encode_properties(spec.properties, values))
                page_id = existing
            else:
                created = await self.client.create_page(
                    spec.data_source_id, encode_properties(spec.properties, values)
                )
                page_id = created["id"]

        page = await self.client.get_page(page_id)
        actual = read_fields(spec, page, list(values))
        expected = expected_fields(values)
        if actual != expected:
            diff = sorted(k for k in expected if actual.get(k) != expected[k])
            return page_id, "mismatch", f"fields differ after write: {diff}"
        return page_id, "verified", ""

    async def _find_existing(self, plan: WritePlan) -> str | None:
        lookup = plan.lookup
        if lookup is None:
            return None
        spec = self.specs[plan.data_source]
        if lookup.kind == "habit_day":
            rows = await HabitRepository(self.client, spec, self.tz).for_day(lookup.day)
            return min(rows, key=lambda r: r.last_edited_time).id if rows else None
        if lookup.kind == "period_start":
            pages = await PeriodRepository(self.client, spec, self.tz).starting_on(lookup.day)
            return pages[0].id if pages else None
        if lookup.kind == "task_title_due":
            repo = TaskRepository(self.client, spec, self.tz)
            title_filter = {"property": spec.prop("title").name, "title": {"equals": lookup.title}}
            tasks = await repo.query(_task_lookup_query(title_filter, spec, lookup.day))
            return tasks[0].id if tasks else None
        return None


def _task_lookup_query(title_filter: dict[str, Any], spec: DataSourceSpec, day) -> QuerySpec:
    if day is None:
        return QuerySpec(title_filter)
    due = {"property": spec.prop("due").name, "date": {"equals": day.isoformat()}}
    return QuerySpec({"and": [title_filter, due]})


class _DependencyFailed(Exception):
    pass


async def take_snapshot(
    client: NotionWriter, specs: dict[str, DataSourceSpec], kind: ActionKind, payload: Payload
) -> tuple[dict[str, Any] | None, datetime | None]:
    """Current values of the fields an update will change, captured when the proposal is made.

    The executor compares them with the page at execution time to detect stale proposals."""
    placeholders = {proposal_id: "pending" for proposal_id in dependencies(payload)}
    plan = build_plan(kind, payload, specs, placeholders)
    if not plan.page_id:
        return None, None
    page = await client.get_page(plan.page_id)
    snapshot = read_fields(specs[plan.data_source], page, list(plan.values))
    return snapshot, parse_notion_datetime(page["last_edited_time"])
