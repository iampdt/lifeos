"""Chat with the Chief of Staff (design §8.5).

A loop of structured steps on the same router as the workflows: each model call returns one
tool call or the final answer. Read tools return Notion data, wrapped as untrusted. Propose tools
only create pending proposals, which the owner approves in the approvals panel; code checks
every argument and the 3-major-tasks rule (R1) before proposing. Limits: 6 steps and 90 s per
turn, 30 s per model call.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import update

from app.agents.common import ask_model
from app.db.models import AgentRun
from app.domain.models import Task
from app.execution.actions import ActionKind, CreateTask, MarkDone, SetStatus
from app.services.calendar import week_range
from app.services.context import (
    DATA_RULES,
    RefMap,
    book_line,
    clean,
    data_block,
    fmt_day,
    replace_refs,
    task_line,
)
from app.services.rules import (
    MAJOR_STATUSES,
    fallback_order,
    free_slots,
    habit_summary,
    is_open,
    overdue,
    week_metrics,
)
from app.workflows.common import proposal_cards
from app.workflows.deps import WorkflowDeps
from app.workflows.proposing import ProposalBuilder

MAX_STEPS = 6
TURN_SECONDS = 90
MAX_HISTORY = 10
UNAVAILABLE = "The AI is unavailable right now. Try again shortly."
OUT_OF_STEPS = "I ran out of steps for this question. Try asking it more narrowly."

ToolName = Literal[
    "get_today", "get_overdue", "get_next_7_days", "get_inbox", "get_week_summary",
    "get_goals", "get_reading", "propose_mark_done", "propose_reschedule",
    "propose_set_status", "propose_create_task",
]  # fmt: skip


class ChatStep(BaseModel):
    action: Literal["tool", "answer"]
    tool: ToolName | None = None
    ref: str | None = Field(default=None, description="Task reference like T3 from a tool result")
    title: str | None = Field(default=None, description="Title for propose_create_task")
    date: str | None = Field(
        default=None, description="YYYY-MM-DD: the new Due date, or a new task's Due date"
    )
    status: Literal["next", "doing", "waiting", "cancelled"] | None = None
    answer: str | None = Field(default=None, description="Your reply to the owner; plain text")


SYSTEM = f"""You are the Chief of Staff in LifeOS, the owner's personal productivity system
built on Notion. Answer questions about their tasks, week, goals and reading, and prepare
changes they ask for.
Each step, either call ONE tool (action "tool") or give the final answer (action "answer").
Tools:
- get_today: open tasks due today or earlier
- get_overdue: open tasks past their Due date
- get_next_7_days: open tasks due in the next 7 days
- get_inbox: tasks waiting in the Inbox
- get_week_summary: this week's planned and done counts and the habit scorecard
- get_goals: active goals with progress
- get_reading: books being read
- propose_mark_done(ref): propose marking a task Done today
- propose_reschedule(ref, date): propose a new Due date
- propose_set_status(ref, status): propose Next, Doing, Waiting or Cancelled
- propose_create_task(title, date optional): propose a new task (Inbox, or Next when dated)
Rules:
- Use tools for facts; never guess task data. References come only from tool results.
- Propose tools don't change Notion: they create proposals the owner approves in the
  Approvals panel. Say a change is "proposed", never that it's done.
- Only propose changes the owner asked for.
- A day holds at most 3 major tasks. If a proposal is refused, explain the reason.
- Answer briefly in plain text and name tasks by title, never by reference code.
{DATA_RULES}"""


class _Turn:
    """Data loaded during one chat turn, so each tool reads Notion at most once."""

    def __init__(self, deps: WorkflowDeps) -> None:
        self.deps = deps
        self.refs = RefMap("T")
        self.tasks: dict[str, Task] = {}
        self._active: list[Task] | None = None

    async def active(self) -> list[Task]:
        if self._active is None:
            self._active = await self.deps.workspace.tasks.active()
        return self._active

    def listing(self, title: str, tasks: Sequence[Task]) -> str:
        lines = []
        for task in tasks:
            self.tasks[task.id] = task
            lines.append(task_line(self.refs.add(task.id), task))
        return f"{title}:\n" + ("\n".join(lines) if lines else "(none)")

    def task(self, ref: str | None) -> Task:
        page_id = self.refs.resolve(ref or "")
        if page_id is None:
            raise _Refused(f"unknown task reference {clean(ref, 20)!r}; list tasks first")
        return self.tasks[page_id]

    def titles(self) -> dict[str, str]:
        return {ref: clean(self.tasks[pid].title, 80) for ref, pid in self.refs.ids.items()}


class _Refused(Exception):
    """A tool call the model can recover from; the reason goes back into the prompt."""


class ChatAgent:
    def __init__(self, deps: WorkflowDeps) -> None:
        self.deps = deps

    async def run(
        self, message: str, history: Sequence[dict[str, str]] = ()
    ) -> AsyncIterator[dict[str, Any]]:
        deps = self.deps
        run_id = uuid.uuid4()
        async with deps.session_factory() as db, db.begin():
            db.add(AgentRun(id=run_id, workflow="chat", trigger="chat",
                            thread_id=str(run_id), status="running"))  # fmt: skip
        yield {"type": "run", "run_id": str(run_id)}

        turn = _Turn(deps)
        results: list[str] = []
        usage: list[dict[str, Any]] = []
        proposal_ids: list[str] = []
        status, error = "completed", None
        started = time.monotonic()
        answered = False
        try:
            for step_number in range(MAX_STEPS):
                if time.monotonic() - started > TURN_SECONDS:
                    break
                prompt = self._prompt(message, history, results, MAX_STEPS - step_number)
                call = await ask_model(
                    deps.llm, ChatStep, agent="chat", system=SYSTEM, prompt=prompt
                )
                if call.value is None:
                    status, error = "failed", {"type": "llm_unavailable"}
                    yield {"type": "error", "code": "llm_unavailable", "message": UNAVAILABLE}
                    break
                usage.append(call.usage or {})
                step = call.value
                if step.action == "answer" or step.tool is None:
                    text = clean(replace_refs(step.answer or "", turn.titles()), 2000)
                    yield {"type": "answer", "text": text or "(no answer)"}
                    answered = True
                    break
                yield {"type": "tool", "name": step.tool}
                try:
                    output, cards = await self._tool(turn, step, run_id)
                except _Refused as exc:
                    output, cards = f"REFUSED: {exc}", []
                    yield {"type": "tool_refused", "name": step.tool, "reason": str(exc)}
                for card in cards:
                    proposal_ids.append(card["id"])
                    yield {"type": "proposal", "proposal": card}
                args = ", ".join(
                    f"{k}={v}" for k, v in
                    (("ref", step.ref), ("date", step.date), ("status", step.status),
                     ("title", clean(step.title, 60) if step.title else None)) if v
                )  # fmt: skip
                results.append(f"{len(results) + 1}. {step.tool}({args}) →\n{output}")
            if not answered and status == "completed":
                yield {"type": "answer", "text": OUT_OF_STEPS}
        except Exception as exc:  # recorded, then surfaced as an error event
            status, error = "failed", {"type": type(exc).__name__, "message": str(exc)[:300]}
            yield {"type": "error", "code": "internal_error", "message": "Chat failed."}
        finally:
            values: dict[str, Any] = {
                "status": status,
                "finished_at": datetime.now(UTC),
                "error": error,
                # No message or answer text is stored: it may contain personal data.
                "output": {
                    "llm_calls": len(usage),
                    "tool_calls": len(results),
                    "proposal_ids": proposal_ids,
                },  # fmt: skip
            }
            if usage:
                values.update(
                    provider=usage[-1].get("provider"), model=usage[-1].get("model"),
                    input_tokens=sum(u.get("input_tokens", 0) for u in usage),
                    output_tokens=sum(u.get("output_tokens", 0) for u in usage),
                    llm_latency_ms=sum(u.get("latency_ms", 0) for u in usage),
                )  # fmt: skip
            async with deps.session_factory() as db, db.begin():
                await db.execute(update(AgentRun).where(AgentRun.id == run_id).values(**values))
        yield {"type": "done", "run_id": str(run_id), "proposals": len(proposal_ids)}

    def _prompt(
        self, message: str, history: Sequence[dict[str, str]], results: list[str], left: int
    ) -> str:
        today = self.deps.today()
        lines = [f"Today: {fmt_day(today)} ({self.deps.tz.key})."]
        recent = list(history)[-MAX_HISTORY:]
        if recent:
            lines.append("CONVERSATION SO FAR:")
            for item in recent:
                who = "Owner" if item.get("role") == "user" else "Assistant"
                lines.append(f"{who}: {clean(item.get('content'), 1500)}")
        lines += ["", "OWNER'S MESSAGE:", clean(message, 2000), "", "TOOL RESULTS THIS TURN:"]
        lines.append("\n\n".join(results) if results else "(none yet)")
        lines += ["", f"Steps left: {left}. Call one tool, or answer."]
        return "\n".join(lines)

    async def _tool(
        self, turn: _Turn, step: ChatStep, run_id: uuid.UUID
    ) -> tuple[str, list[dict[str, Any]]]:
        deps, vocab, ws = self.deps, self.deps.vocab, self.deps.workspace
        today = deps.today()
        match step.tool:
            case "get_today":
                tasks = [t for t in await turn.active() if t.due and t.due <= today]
                listing = turn.listing("TODAY AND EARLIER", fallback_order(tasks, vocab))
                return data_block(listing), []
            case "get_overdue":
                late = overdue(await turn.active(), today, vocab)
                return data_block(turn.listing("OVERDUE", late)), []
            case "get_next_7_days":
                soon = [t for t in await turn.active()
                        if t.due and today <= t.due <= today + timedelta(days=7)]  # fmt: skip
                soon.sort(key=lambda t: t.due or date.max)
                return data_block(turn.listing("NEXT 7 DAYS", soon)), []
            case "get_inbox":
                inbox = [t for t in await turn.active() if vocab.status_key(t) == "inbox"]
                return data_block(turn.listing("INBOX", inbox)), []
            case "get_week_summary":
                start, end = week_range(today)
                metrics = week_metrics(await ws.tasks.due_between(start, end), vocab)
                habits = habit_summary(await ws.habits.all(), start, end)
                text = (
                    f"WEEK {start.isoformat()} to {end.isoformat()}: planned {metrics.planned}, "
                    f"done {metrics.done}, still open {metrics.still_open}, "
                    f"cancelled {metrics.cancelled}.\nHABITS: {habits.days_logged} days logged; "
                    f"checks {habits.checks or 'none'}; avg sleep {habits.avg_sleep}; "
                    f"avg screen time {habits.avg_screen_time}"
                )
                return data_block(text), []
            case "get_goals":
                if not ws.goals:
                    return "Goals aren't connected in this workspace.", []
                goals = await ws.goals.active()
                lines = [f"- {clean(g.title)} ({g.type or 'goal'}; {_num(g.current)}/"
                         f"{_num(g.target)})" for g in goals]  # fmt: skip
                return data_block("ACTIVE GOALS:\n" + ("\n".join(lines) or "(none)")), []
            case "get_reading":
                if not ws.books:
                    return "Books aren't connected in this workspace.", []
                reading = deps.specs["books"].prop("status").value("reading")
                books = [b for b in await ws.books.queue() if b.status == reading]
                lines = [book_line(f"B{i}", b) for i, b in enumerate(books, 1)]
                return data_block("READING:\n" + ("\n".join(lines) or "(none)")), []
        return await self._propose(turn, step, run_id, today)

    async def _propose(
        self, turn: _Turn, step: ChatStep, run_id: uuid.UUID, today: date
    ) -> tuple[str, list[dict[str, Any]]]:
        deps, vocab = self.deps, self.deps.vocab
        builder = ProposalBuilder(deps, deps.resolver(today))
        reason = "You asked for this in chat."
        match step.tool:
            case "propose_mark_done":
                task = turn.task(step.ref)
                if not is_open(task, vocab):
                    raise _Refused(f"{task.title!r} is already {task.status}")
                await builder.add(ActionKind.TASK_MARK_DONE,
                                  MarkDone(page_id=task.id, completed_on=today),
                                  reason=reason, rule_ids=("R5",), title=task.title)  # fmt: skip
            case "propose_reschedule":
                task = turn.task(step.ref)
                day = self._future_day(step.date, today)
                if vocab.status_key(task) in MAJOR_STATUSES and not task.recurring:
                    others = [t for t in await turn.active() if t.id != task.id]
                    if free_slots(others, day, vocab) == 0:
                        raise _Refused(f"{fmt_day(day)} already has 3 major tasks (R1)")
                await builder.set_due(task.id, day, "reschedule", reason=reason,
                                      rule_ids=("R1",), title=task.title)  # fmt: skip
            case "propose_set_status":
                task = turn.task(step.ref)
                if step.status is None:
                    raise _Refused("status is required")
                await builder.add(ActionKind.TASK_SET_STATUS,
                                  SetStatus(page_id=task.id, status=step.status),
                                  reason=reason, title=task.title)  # fmt: skip
            case "propose_create_task":
                title = clean(step.title, 200)
                if not title:
                    raise _Refused("a title is required")
                due = self._future_day(step.date, today) if step.date else None
                if due and free_slots(await turn.active(), due, vocab) == 0:
                    raise _Refused(f"{fmt_day(due)} already has 3 major tasks (R1)")
                payload = CreateTask(title=title, status="next" if due else "inbox", due=due)
                await builder.add(ActionKind.TASK_CREATE, payload, reason=reason,
                                  rule_ids=("R11",) if not due else ("R1",))  # fmt: skip
            case _:
                raise _Refused(f"unknown tool {step.tool!r}")
        ids = await builder.commit(run_id=run_id, ttl=timedelta(hours=24), actor="agent:chat")
        cards = await proposal_cards(deps, [str(i) for i in ids], {})
        summary = "; ".join(f"{c['kind']} for {c['target']['title']!r}" for c in cards)
        return f"Proposed (pending the owner's approval): {summary}", cards

    @staticmethod
    def _future_day(raw: str | None, today: date) -> date:
        try:
            day = date.fromisoformat(raw or "")
        except ValueError as exc:
            raise _Refused("date must be YYYY-MM-DD") from exc
        if day < today:
            raise _Refused("the date is in the past")
        return day


def _num(value: float | None) -> str:
    return "?" if value is None else f"{value:g}"
