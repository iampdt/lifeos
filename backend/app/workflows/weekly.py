"""Sunday weekly review, MVP (architecture §5.3).

load → draft (AI narrative + advice) → ⏸ decide (overdue queue and Inbox) → propose
→ ⏸ approve → execute → summarize

The only place overdue tasks are moved (R6): each gets the owner's decision — carry forward
(earliest day next week with a free slot, R1), reschedule to a date, cancel, or skip. Inbox
items are kept, made Next, or cancelled (R11). The week is closed with its Review text and next
week's page is created if missing. Goals are never changed.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import date, timedelta
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.agents.reviewer import draft_review, render_review
from app.execution.actions import ActionKind, ClosePeriod, SetStatus
from app.services.calendar import week_range, week_title
from app.services.context import RefMap
from app.services.rules import (
    AreaCount,
    HabitSummary,
    WeekMetrics,
    allocate_days,
    habit_summary,
    overdue,
    week_metrics,
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

NAME = "weekly_review"
MAX_OVERDUE_DECISIONS = 15  # R9: keep the review within 20–25 minutes
MAX_INBOX_DECISIONS = 10
OVERDUE_CHOICES = {"carry_forward", "reschedule", "cancel", "skip"}
INBOX_CHOICES = {"keep", "next", "cancel"}


class WeeklyState(BaseState, total=False):
    week: dict[str, str]  # start, end, label
    tasks: list[dict[str, Any]]  # every open task
    done_tasks: list[dict[str, Any]]  # done tasks due this week
    overdue_refs: dict[str, str]
    inbox_refs: dict[str, str]
    suggested: dict[str, str | None]  # overdue task id → carry-forward date
    metrics: dict[str, Any]
    habits: dict[str, Any]
    week_page: dict[str, Any] | None
    review: dict[str, Any]
    choices: dict[str, Any]


def build_graph(deps: WorkflowDeps) -> StateGraph:
    ws, vocab = deps.workspace, deps.vocab

    async def load(state: WeeklyState) -> dict[str, Any]:
        today = deps.today()
        inputs = state.get("inputs") or {}
        anchor = date.fromisoformat(inputs["week_of"]) if inputs.get("week_of") else today
        start, end = week_range(anchor)
        in_week = await ws.tasks.due_between(start, end)
        active = await ws.tasks.active()
        rows = await ws.habits.all()
        queue = overdue(active, end + timedelta(days=1), vocab)
        warnings = []
        if len(queue) > MAX_OVERDUE_DECISIONS:
            warnings.append(
                f"{len(queue) - MAX_OVERDUE_DECISIONS} more overdue tasks stay in the queue "
                "for next time (R9 keeps the review short)."
            )
            queue = queue[:MAX_OVERDUE_DECISIONS]
        inbox = [t for t in active if vocab.status_key(t) == "inbox"][:MAX_INBOX_DECISIONS]
        first = max(end + timedelta(days=1), today + timedelta(days=1))
        suggested = allocate_days(queue, active, first, first + timedelta(days=6), vocab)
        overdue_refs, inbox_refs = RefMap("T"), RefMap("I")
        for task in queue:
            overdue_refs.add(task.id)
        for task in inbox:
            inbox_refs.add(task.id)
        metrics = week_metrics(in_week, vocab)
        this_week = habit_summary(rows, start, end)
        last_week = habit_summary(rows, start - timedelta(days=7), start - timedelta(days=1))
        week_page = None
        if ws.weeks:
            pages = await ws.weeks.covering(start)
            if pages:
                week_page = {"id": pages[0].id, "review": pages[0].review}
        done = [t for t in in_week if vocab.status_key(t) == "done"]
        return {
            "today": today.isoformat(),
            "week": {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "label": week_title(start),
            },  # fmt: skip
            "tasks": dump_tasks(active),
            "done_tasks": dump_tasks(done),
            "overdue_refs": overdue_refs.ids,
            "inbox_refs": inbox_refs.ids,
            "suggested": {k: v.isoformat() if v else None for k, v in suggested.items()},
            "metrics": _metrics_dict(metrics),
            "habits": {"this_week": asdict(this_week), "last_week": asdict(last_week)},
            "week_page": week_page,
            "titles": {t.id: t.title for t in [*active, *in_week]},
            "warnings": warnings,
        }

    async def draft(state: WeeklyState) -> dict[str, Any]:
        tasks = {t.id: t for t in load_tasks(state["tasks"])}
        done = load_tasks(state["done_tasks"])
        queue = [tasks[i] for i in state["overdue_refs"].values()]
        goals = await ws.goals.active() if ws.goals else []
        result = await draft_review(
            deps.llm,
            week_label=state["week"]["label"],
            metrics=_metrics_obj(state["metrics"]),
            habits=HabitSummary(**state["habits"]["this_week"]),
            previous_habits=HabitSummary(**state["habits"]["last_week"]),
            done=done,
            overdue=queue,
            goals=goals,
        )
        review = {k: v for k, v in result.as_dict().items() if k not in ("warnings", "usage")}
        return {"review": review, "warnings": result.warnings, "llm": result.usage}

    async def decide(state: WeeklyState) -> dict[str, Any]:
        tasks = {t.id: t for t in load_tasks(state["tasks"])}
        review = state["review"]
        advice = review.get("advice") or {}

        def row(ref: str, task_id: str) -> dict[str, Any]:
            task = tasks[task_id]
            return {"ref": ref, "id": task_id, "title": task.title, "status": task.status,
                    "priority": task.priority,
                    "due": task.due.isoformat() if task.due else None}  # fmt: skip

        answer = interrupt(
            {
                "type": "decisions",
                "workflow": NAME,
                "week": state["week"],
                "metrics": state["metrics"],
                "habits": state["habits"],
                "review": {k: review.get(k) for k in
                           ("summary", "wins", "misses", "bottlenecks", "priorities", "source")},
                "overdue": [
                    {**row(ref, tid),
                     "recommended": (advice.get(tid) or {}).get("decision", "carry_forward"),
                     "reason": (advice.get(tid) or {}).get("reason", ""),
                     "suggested_due": state["suggested"].get(tid)}
                    for ref, tid in state["overdue_refs"].items()
                ],
                "inbox": [row(ref, tid) for ref, tid in state["inbox_refs"].items()],
                "accepts": {
                    "accept": "true to take every recommendation (Inbox items are kept)",
                    "overdue": "{ref: {decision: carry_forward|reschedule|cancel|skip, "
                               "due?: YYYY-MM-DD}}",
                    "inbox": "{ref: {action: keep|next|cancel, due?: YYYY-MM-DD}}",
                    "note": "optional; your own words, added to the week's Review",
                },
            }
        )  # fmt: skip
        today = date.fromisoformat(state["today"])
        choices, warnings = _resolve_choices(answer, state, advice, today)
        return {"choices": choices, "warnings": warnings}

    async def propose(state: WeeklyState) -> dict[str, Any]:
        today = date.fromisoformat(state["today"])
        choices = state["choices"]
        resolver = deps.resolver(today)
        builder = ProposalBuilder(deps, resolver)
        warnings: list[str] = []
        for task_id, choice in choices["overdue"].items():
            decision = choice["decision"]
            if decision == "cancel":
                await builder.add(
                    ActionKind.TASK_SET_STATUS,
                    SetStatus(page_id=task_id, status="cancelled"),
                    reason="Your weekly-review decision: cancel.",
                    rule_ids=("R6",),
                    title=state["titles"].get(task_id),
                )
            elif decision in ("carry_forward", "reschedule"):
                if not choice.get("due"):
                    title = state["titles"].get(task_id, task_id)
                    warnings.append(f"No free day next week for {title!r}; left in the queue.")
                    continue
                await builder.set_due(
                    task_id, date.fromisoformat(choice["due"]), decision,
                    reason=f"Your weekly-review decision: {decision.replace('_', ' ')}.",
                    rule_ids=("R1", "R6"), title=state["titles"].get(task_id),
                )  # fmt: skip
        for task_id, choice in choices["inbox"].items():
            if choice["action"] == "keep":
                continue
            status = "next" if choice["action"] == "next" else "cancelled"
            await builder.add(
                ActionKind.TASK_SET_STATUS,
                SetStatus(page_id=task_id, status=status),
                reason=f"Inbox triage: {choice['action']}.",
                rule_ids=("R11",),
                title=state["titles"].get(task_id),
            )
            if status == "next" and choice.get("due"):
                await builder.set_due(
                    task_id, date.fromisoformat(choice["due"]), "reschedule",
                    reason="Inbox triage: planned.", rule_ids=("R11",),
                    title=state["titles"].get(task_id),
                )  # fmt: skip
        page = state.get("week_page")
        if page:
            text = render_review(
                state["week"]["label"],
                state["review"],
                _metrics_obj(state["metrics"]),
                HabitSummary(**state["habits"]["this_week"]),
                choices.get("note"),
            )
            if page.get("review"):
                # Keep what the owner already wrote; LifeOS's review goes underneath.
                text = f"{page['review'].rstrip()}\n\n{text}"
            await builder.add(
                ActionKind.WEEK_CLOSE,
                ClosePeriod(page_id=page["id"], review=text[:6000]),
                reason="Close the week with its review.",
                rule_ids=("R9",),
                title=state["week"]["label"],
            )
        else:
            warnings.append("This week has no Weekly Progress page, so there is nothing to close.")
        next_start = date.fromisoformat(state["week"]["end"]) + timedelta(days=1)
        await builder.ensure_period(
            await resolver.resolve("week", next_start), "Next week's Weekly Progress page."
        )
        ids = await builder.commit(
            run_id=run_uuid(state), ttl=timedelta(days=7), actor=f"agent:{NAME}"
        )
        return {"proposal_ids": [str(i) for i in ids], "warnings": warnings}

    async def summarize(state: WeeklyState) -> dict[str, Any]:
        review = state.get("review") or {}
        choices = state.get("choices") or {"overdue": {}, "inbox": {}}
        decisions = state.get("decisions") or {}
        counted: dict[str, int] = {}
        for choice in choices["overdue"].values():
            counted[choice["decision"]] = counted.get(choice["decision"], 0) + 1
        summary = {
            "workflow": NAME,
            "week": state.get("week"),
            "metrics": state.get("metrics"),
            "review": {k: review.get(k) for k in
                       ("summary", "wins", "misses", "bottlenecks", "priorities", "source")},
            "overdue_decisions": counted,
            "inbox_decisions": {
                a: sum(1 for c in choices["inbox"].values() if c["action"] == a)
                for a in sorted(INBOX_CHOICES)
            },
            "proposals": {
                "approved": len(decisions.get("approved") or []),
                "rejected": len(decisions.get("rejected") or []),
                "errors": decisions.get("errors") or {},
            },
            "execution": outcome_counts(state.get("results") or []),
            "notes": [n for n in [execution_note(state, deps.writes_enabled)] if n],
            "warnings": state.get("warnings") or [],
        }  # fmt: skip
        return {"summary": summary}

    graph = StateGraph(WeeklyState)
    for node in (load, draft, decide, propose, summarize):
        graph.add_node(node.__name__, node)
    graph.add_edge(START, "load")
    graph.add_edge("load", "draft")
    graph.add_edge("draft", "decide")
    graph.add_edge("decide", "propose")
    add_approval_flow(graph, deps, NAME, after="propose", then="summarize")
    graph.add_edge("summarize", END)
    return graph


def _metrics_dict(metrics: WeekMetrics) -> dict[str, Any]:
    data = asdict(metrics)
    data["completion_rate"] = metrics.completion_rate
    return data


def _metrics_obj(data: dict[str, Any]) -> WeekMetrics:
    return WeekMetrics(
        planned=data["planned"],
        done=data["done"],
        cancelled=data["cancelled"],
        still_open=data["still_open"],
        by_area={k: AreaCount(**v) for k, v in data["by_area"].items()},
    )


def _parse_day(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


def _resolve_choices(
    answer: Any, state: WeeklyState, advice: dict[str, Any], today: date
) -> tuple[dict[str, Any], list[str]]:
    """Owner's answers by reference, defaulting to the recommendations. Dates must be after
    today; an invalid one falls back to the suggested carry-forward day."""
    answer = answer if isinstance(answer, dict) else {}
    given_overdue = answer.get("overdue") if isinstance(answer.get("overdue"), dict) else {}
    given_inbox = answer.get("inbox") if isinstance(answer.get("inbox"), dict) else {}
    warnings: list[str] = []
    overdue_choices: dict[str, dict[str, Any]] = {}
    for ref, task_id in state["overdue_refs"].items():
        default = (advice.get(task_id) or {}).get("decision", "carry_forward")
        given = given_overdue.get(ref) if isinstance(given_overdue.get(ref), dict) else {}
        decision = given.get("decision", default)
        if decision not in OVERDUE_CHOICES:
            warnings.append(f"{ref}: unknown decision {str(decision)[:20]!r}; used {default}.")
            decision = default
        due = _parse_day(given.get("due"))
        if due is not None and due <= today:
            warnings.append(f"{ref}: {due} is not in the future; used the suggested day.")
            due = None
        if decision == "reschedule" and due is None:
            decision = "carry_forward"
        if decision == "carry_forward" and due is None:
            due = _parse_day(state["suggested"].get(task_id))
        overdue_choices[task_id] = {"decision": decision,
                                    "due": due.isoformat() if due else None}  # fmt: skip
    inbox_choices: dict[str, dict[str, Any]] = {}
    for ref, task_id in state["inbox_refs"].items():
        given = given_inbox.get(ref) if isinstance(given_inbox.get(ref), dict) else {}
        action = given.get("action", "keep")
        if action not in INBOX_CHOICES:
            warnings.append(f"{ref}: unknown action {str(action)[:20]!r}; kept in the Inbox.")
            action = "keep"
        due = _parse_day(given.get("due"))
        if due is not None and due <= today:
            due = None
        inbox_choices[task_id] = {"action": action, "due": due.isoformat() if due else None}
    note = str(answer.get("note") or "").strip()[:1000] or None
    return {"overdue": overdue_choices, "inbox": inbox_choices, "note": note}, warnings
