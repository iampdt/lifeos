"""Evening check-in and nightly plan (architecture §5.1).

load → ⏸ ask "what did you finish?" → parse → plan tomorrow → propose → ⏸ approve → execute
→ summarize

Unfinished tasks are only noted for the Sunday review (R6); the nightly plan fills tomorrow's
free major-task slots (R1) from undated or later tasks, never from overdue ones.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import date, timedelta
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.agents.checkin import parse_checkin
from app.agents.planner import plan_day
from app.domain.models import Book
from app.execution.actions import (
    ActionKind,
    CreateTask,
    MarkDone,
    UpdateBookProgress,
    UpsertHabitDay,
)
from app.services.context import RefMap
from app.services.rules import (
    fallback_order,
    major_tasks,
    planning_candidates,
)
from app.workflows.common import (
    BaseState,
    add_approval_flow,
    dump_tasks,
    execution_note,
    load_tasks,
    outcome_counts,
    run_uuid,
)
from app.workflows.deps import WorkflowDeps
from app.workflows.proposing import ProposalBuilder

NAME = "evening_checkin"


def question(deps: WorkflowDeps) -> str:
    """The evening question, naming only what this workspace tracks."""
    things = []
    if deps.scorecard.habits:
        things.append("habits")
    things += [m.name.lower() for m in deps.scorecard.metrics]
    if deps.workspace.books:
        things.append("pages read")
    things += ["what you learned", "anything new to capture"]
    return f"What did you finish today? Mention {', '.join(things[:-1])} and {things[-1]}."


class EveningState(BaseState, total=False):
    tasks: list[dict[str, Any]]  # every open task
    shown_ids: list[str]  # the tasks shown in the question
    task_refs: dict[str, str]
    books: list[dict[str, Any]]
    book_refs: dict[str, str]
    habit_today: dict[str, Any] | None
    answer: dict[str, Any]
    checkin: dict[str, Any]
    plan: dict[str, Any]


def build_graph(deps: WorkflowDeps) -> StateGraph:
    ws, vocab = deps.workspace, deps.vocab

    async def load(state: EveningState) -> dict[str, Any]:
        today = deps.today()
        active = await ws.tasks.active()
        shown = fallback_order(
            [t for t in active
             if t.title.strip()  # an untitled task can't be named in a reply
             and ((t.due is not None and t.due <= today) or vocab.status_key(t) == "doing")],
            vocab,
        )  # fmt: skip
        task_refs = RefMap("T")
        for task in shown:
            task_refs.add(task.id)
        books: list[Book] = []
        if ws.books:
            reading = ws.books.spec.prop("status").value("reading")
            books = [b for b in await ws.books.queue() if b.status == reading]
        book_refs = RefMap("B")
        for book in books:
            book_refs.add(book.id)
        rows = await ws.habits.for_day(today) if ws.habits else []
        rows.sort(key=lambda r: r.last_edited_time)
        habit_today = (
            {"checks": rows[0].checks, "metrics": rows[0].metrics, "notes": rows[0].notes}
            if rows else None
        )  # fmt: skip
        return {
            "today": today.isoformat(),
            "tasks": dump_tasks(active),
            "shown_ids": [t.id for t in shown],
            "task_refs": task_refs.ids,
            "books": [b.model_dump(mode="json") for b in books],
            "book_refs": book_refs.ids,
            "habit_today": habit_today,
            "titles": {**{t.id: t.title for t in active}, **{b.id: b.title for b in books}},
        }

    async def ask(state: EveningState) -> dict[str, Any]:
        tasks = {t.id: t for t in load_tasks(state["tasks"])}
        refs = state["task_refs"]
        books = {b["id"]: b for b in state.get("books") or []}
        answer = interrupt(
            {
                "type": "question",
                "workflow": NAME,
                "prompt": question(deps),
                "tasks": [
                    {"ref": ref, "id": pid, "title": tasks[pid].title,
                     "status": tasks[pid].status, "priority": tasks[pid].priority,
                     "due": tasks[pid].due.isoformat() if tasks[pid].due else None}
                    for ref, pid in refs.items()
                ],
                "books": [{"ref": ref, "id": bid, "title": books[bid]["title"]}
                          for ref, bid in (state.get("book_refs") or {}).items()],
                "habits": (
                    {**deps.scorecard.describe(), "logged_today": state.get("habit_today")}
                    if deps.scorecard else None
                ),
                "accepts": {
                    "text": "free text",
                    "done_refs": "optional list of task refs, e.g. ['T1']",
                    "habits": "optional {habit key: true|false}",
                    "metrics": "optional {metric key: number}",
                },
            }
        )  # fmt: skip
        if not isinstance(answer, dict):
            answer = {"text": str(answer or "")}
        return {"answer": answer}

    async def parse(state: EveningState) -> dict[str, Any]:
        tasks = {t.id: t for t in load_tasks(state["tasks"])}
        books = [Book.model_validate(b) for b in state.get("books") or []]
        result = await parse_checkin(
            deps.llm,
            state.get("answer") or {},
            tasks=[tasks[i] for i in state["shown_ids"]],
            books=books,
            task_refs=RefMap("T", dict(state["task_refs"])),
            book_refs=RefMap("B", dict(state.get("book_refs") or {})),
            scorecard=deps.scorecard,
        )
        checkin = {k: v for k, v in asdict(result).items() if k not in ("warnings", "usage")}
        return {"checkin": checkin, "warnings": result.warnings, "llm": result.usage}

    async def plan(state: EveningState) -> dict[str, Any]:
        today = date.fromisoformat(state["today"])
        tomorrow = today + timedelta(days=1)
        if (state.get("inputs") or {}).get("plan_tomorrow") is False:
            return {"plan": {"day": tomorrow.isoformat(), "skipped": True}}
        done = set(state["checkin"]["done_ids"])
        remaining = [t for t in load_tasks(state["tasks"]) if t.id not in done]
        committed = major_tasks(remaining, tomorrow, vocab)
        slots = deps.rules.max_major_per_day - len(committed)
        candidates = planning_candidates(remaining, tomorrow, vocab)
        goals = await ws.goals.active() if ws.goals and slots > 0 and candidates else []
        result = await plan_day(
            deps.llm, day=tomorrow, slots=slots, candidates=candidates,
            committed=committed, goals=goals,
        )  # fmt: skip
        return {
            "plan": {
                "day": tomorrow.isoformat(),
                "committed_ids": [t.id for t in committed],
                "pick_ids": result.task_ids,
                "reasons": result.reasons,
                "note": result.note,
                "source": result.source,
                "slots": slots,
                "candidates": len(candidates),
            },
            "warnings": result.warnings,
            "llm": result.usage,
        }

    async def propose(state: EveningState) -> dict[str, Any]:
        today = date.fromisoformat(state["today"])
        checkin, plan_ = state["checkin"], state["plan"]
        builder = ProposalBuilder(deps, deps.resolver(today))
        for task_id in checkin["done_ids"]:
            await builder.add(
                ActionKind.TASK_MARK_DONE,
                MarkDone(page_id=task_id, completed_on=today),
                reason="You said it's finished in tonight's check-in.",
                rule_ids=("R5",),
                title=state["titles"].get(task_id),
            )
        habit = _habit_payload(today, checkin, state.get("habit_today")) if ws.habits else None
        if habit:
            await builder.add(ActionKind.HABIT_UPSERT_DAY, habit, reason="From tonight's check-in.")
        books = {b["id"]: Book.model_validate(b) for b in state.get("books") or []}
        for item in checkin["reading"]:
            book = books[item["book_id"]]
            total = (book.pages_read or 0) + item["pages"]
            if book.pages:
                total = min(total, book.pages)
            await builder.add(
                ActionKind.BOOK_UPDATE_PROGRESS,
                UpdateBookProgress(page_id=book.id, pages_read=total),
                reason=f"You read {item['pages']} pages today.",
                title=book.title,
            )
        for title in checkin["new_tasks"]:
            await builder.add(
                ActionKind.TASK_CREATE,
                CreateTask(title=title, status="inbox"),
                reason="Captured from tonight's check-in; triage it in the weekly review.",
                rule_ids=("R11",),
            )
        if not plan_.get("skipped"):
            tomorrow = date.fromisoformat(plan_["day"])
            for task_id in plan_["pick_ids"]:
                await builder.set_due(
                    task_id, tomorrow, "reschedule",
                    reason=plan_["reasons"].get(task_id) or "Planned for tomorrow.",
                    rule_ids=("R1",), title=state["titles"].get(task_id),
                )  # fmt: skip
        ids = await builder.commit(
            run_id=run_uuid(state), ttl=timedelta(hours=24), actor=f"agent:{NAME}"
        )
        return {"proposal_ids": [str(i) for i in ids], "warnings": builder.notes()}

    async def summarize(state: EveningState) -> dict[str, Any]:
        titles = state.get("titles") or {}
        checkin, plan_ = state.get("checkin") or {}, state.get("plan") or {}
        results = state.get("results") or []
        decisions = state.get("decisions") or {}
        name = lambda ids: [titles.get(i, i) for i in ids or []]  # noqa: E731
        notes = [n for n in [execution_note(state, deps.writes_enabled)] if n]
        if checkin.get("partial_ids"):
            notes.append("Unfinished tasks stay where they are; decide on them on Sunday (R6).")
        summary = {
            "workflow": NAME,
            "day": state.get("today"),
            "done": name(checkin.get("done_ids")),
            "partial": name(checkin.get("partial_ids")),
            # Display names, not mapping keys.
            "habits": {
                deps.scorecard.habit_name(k): v for k, v in (checkin.get("habits") or {}).items()
            },
            "metrics": [
                deps.scorecard.metric(k).label(v) for k, v in (checkin.get("metrics") or {}).items()
            ],
            "parsed_by": checkin.get("source"),
            "tomorrow": {
                "day": plan_.get("day"),
                "committed": name(plan_.get("committed_ids")),
                "proposed": name(plan_.get("pick_ids")),
                "planned_by": plan_.get("source"),
                "note": plan_.get("note") or "",
            },
            "proposals": {
                "approved": len(decisions.get("approved") or []),
                "rejected": len(decisions.get("rejected") or []),
                "errors": decisions.get("errors") or {},
            },
            "execution": outcome_counts(results),
            "notes": notes,
            "warnings": state.get("warnings") or [],
        }
        return {"summary": summary}

    graph = StateGraph(EveningState)
    for node in (load, ask, parse, plan, propose, summarize):
        graph.add_node(node.__name__, node)
    graph.add_edge(START, "load")
    graph.add_edge("load", "ask")
    graph.add_edge("ask", "parse")
    graph.add_edge("parse", "plan")
    graph.add_edge("plan", "propose")
    add_approval_flow(graph, deps, NAME, after="propose", then="summarize")
    graph.add_edge("summarize", END)
    return graph


def _habit_payload(
    today: date, checkin: dict[str, Any], existing: dict[str, Any] | None
) -> UpsertHabitDay | None:
    learned = checkin.get("learned")
    metrics = checkin.get("metrics") or {}
    if not (checkin["habits"] or metrics or learned):
        return None
    notes = None
    if learned:
        # Append to notes the owner already wrote today instead of replacing them.
        previous = (existing or {}).get("notes") or ""
        notes = learned if not previous or learned in previous else f"{previous}\n{learned}"
    return UpsertHabitDay(day=today, checks=checkin["habits"], metrics=metrics, notes=notes)
