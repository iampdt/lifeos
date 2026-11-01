"""Start, resume and inspect workflow runs; keep `agent_runs` in step with the graph.

Each run is a LangGraph thread (thread_id = run id) checkpointed by the configured saver, so a
run can wait for the owner for hours. Resuming is a compare-and-set on the run's status, so two
clients answering at once can't both advance the same run.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.types import Command
from sqlalchemy import select, update

from app.db.models import AgentRun
from app.workflows import evening, morning, weekly
from app.workflows.deps import WorkflowDeps

logger = logging.getLogger("lifeos.workflows")

BUILDERS = {
    evening.NAME: evening.build_graph,
    morning.NAME: morning.build_graph,
    weekly.NAME: weekly.build_graph,
}
WAITING = {
    "question": "waiting_input",
    "decisions": "waiting_input",
    "approval": "waiting_approval",
}


class UnknownWorkflow(Exception):
    pass


class RunStateError(Exception):
    """The run doesn't exist or isn't waiting for an answer."""


@dataclass(frozen=True)
class RunView:
    id: str
    workflow: str
    status: str
    waiting_for: dict[str, Any] | None
    summary: dict[str, Any] | None
    error: dict[str, Any] | None
    usage: dict[str, Any]
    trigger: str = "manual"
    started_at: str | None = None
    finished_at: str | None = None

    @classmethod
    def from_row(cls, row: AgentRun) -> RunView:
        output = row.output or {}
        return cls(
            id=str(row.id),
            workflow=row.workflow,
            status=row.status,
            waiting_for=output.get("waiting_for"),
            summary=output.get("summary"),
            error=row.error,
            usage={
                "provider": row.provider,
                "model": row.model,
                "input_tokens": row.input_tokens,
                "output_tokens": row.output_tokens,
                "llm_latency_ms": row.llm_latency_ms,
                "llm_calls": output.get("llm_calls", 0),
            },
            trigger=row.trigger,
            started_at=row.started_at.isoformat() if row.started_at else None,
            finished_at=row.finished_at.isoformat() if row.finished_at else None,
        )


class WorkflowRunner:
    def __init__(self, deps: WorkflowDeps, checkpointer: BaseCheckpointSaver) -> None:
        self.deps = deps
        self.graphs = {
            name: build(deps).compile(checkpointer=checkpointer) for name, build in BUILDERS.items()
        }

    async def start(
        self, workflow: str, *, trigger: str = "manual", inputs: dict[str, Any] | None = None
    ) -> RunView:
        if workflow not in self.graphs:
            raise UnknownWorkflow(workflow)
        run_id = uuid.uuid4()
        async with self.deps.session_factory() as session, session.begin():
            session.add(
                AgentRun(id=run_id, workflow=workflow, trigger=trigger,
                         thread_id=str(run_id), status="running")
            )  # fmt: skip
        return await self._advance(
            run_id, workflow, {"run_id": str(run_id), "inputs": inputs or {}}
        )

    async def resume(self, run_id: uuid.UUID, answer: Any) -> RunView:
        async with self.deps.session_factory() as session, session.begin():
            workflow = await session.scalar(
                update(AgentRun)
                .where(AgentRun.id == run_id, AgentRun.status.in_(set(WAITING.values())))
                .values(status="running")
                .returning(AgentRun.workflow)
            )
        if workflow is None:
            raise RunStateError("run not found or not waiting for an answer")
        return await self._advance(run_id, workflow, Command(resume=answer))

    async def recent(self, *, limit: int = 20, status: str | None = None) -> list[RunView]:
        async with self.deps.session_factory() as session:
            query = select(AgentRun).order_by(AgentRun.started_at.desc()).limit(limit)
            if status:
                query = query.where(AgentRun.status == status)
            return [RunView.from_row(row) for row in await session.scalars(query)]

    async def get(self, run_id: uuid.UUID) -> RunView:
        async with self.deps.session_factory() as session:
            row = await session.get(AgentRun, run_id)
        if row is None:
            raise RunStateError("run not found")
        return RunView.from_row(row)

    async def _advance(self, run_id: uuid.UUID, workflow: str, payload: Any) -> RunView:
        config = {"configurable": {"thread_id": str(run_id)}}
        values: dict[str, Any] = {"status": "running"}
        try:
            result = await self.graphs[workflow].ainvoke(payload, config)
        except Exception as exc:
            logger.exception("workflow %s run %s failed", workflow, run_id)
            values.update(
                status="failed",
                finished_at=datetime.now(UTC),
                error={"type": type(exc).__name__, "message": str(exc)[:500]},
            )
            result = (await self.graphs[workflow].aget_state(config)).values
        else:
            interrupts = result.get("__interrupt__") or []
            if interrupts:
                request = interrupts[0].value
                values["status"] = WAITING.get(request.get("type"), "waiting_input")
                values["output"] = {"waiting_for": request}
            else:
                values.update(status="completed", finished_at=datetime.now(UTC),
                              output={"summary": result.get("summary")})  # fmt: skip
        calls = result.get("llm") or []
        if calls:
            last = calls[-1]
            values.update(
                provider=last.get("provider"),
                model=last.get("model"),
                input_tokens=sum(c.get("input_tokens", 0) for c in calls),
                output_tokens=sum(c.get("output_tokens", 0) for c in calls),
                llm_latency_ms=sum(c.get("latency_ms", 0) for c in calls),
            )
        values["output"] = {**(values.get("output") or {}), "llm_calls": len(calls)}
        async with self.deps.session_factory() as session, session.begin():
            await session.execute(update(AgentRun).where(AgentRun.id == run_id).values(**values))
            row = await session.get(AgentRun, run_id)
            assert row is not None
            return RunView.from_row(row)
