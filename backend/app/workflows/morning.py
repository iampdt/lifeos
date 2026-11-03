"""Morning briefing (architecture §5.2).

load → plan → propose → ⏸ approve (only if something was proposed) → execute → brief

Planning depends on today's major tasks (R1):
- more than 3: choose 3 to keep and offer the rest for the next days with a free slot;
- none: fill today from undated or later tasks;
- 1–3: leave the day as it is.
Overdue tasks are only counted and pointed to the weekly review (R6).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.agents.planner import plan_day
from app.services.calendar import week_range
from app.services.rules import (
    allocate_days,
    fallback_order,
    is_open,
    major_tasks,
    missing_scorecard_days,
    overdue,
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

NAME = "morning_briefing"


class MorningState(BaseState, total=False):
    tasks: list[dict[str, Any]]
    done_this_week: int
    missing_scorecards: list[str]
    plan: dict[str, Any]


def build_graph(deps: WorkflowDeps) -> StateGraph:
    ws, vocab = deps.workspace, deps.vocab

    async def load(state: MorningState) -> dict[str, Any]:
        today = deps.today()
        active = await ws.tasks.active()
        week_start, _ = week_range(today)
        done = await ws.tasks.done_due_between(week_start, today)
        rows = await ws.habits.all() if ws.habits else None
        missing = (
            missing_scorecard_days(rows, today - timedelta(days=3), today - timedelta(days=1))
            if rows is not None
            else []
        )
        return {
            "today": today.isoformat(),
            "tasks": dump_tasks(active),
            "done_this_week": len(done),
            "missing_scorecards": [d.isoformat() for d in missing],
            "titles": {t.id: t.title for t in active},
        }

    async def plan(state: MorningState) -> dict[str, Any]:
        today = date.fromisoformat(state["today"])
        active = load_tasks(state["tasks"])
        majors = major_tasks(active, today, vocab)
        goals = await ws.goals.active() if ws.goals else []
        if len(majors) > deps.rules.max_major_per_day:
            result = await plan_day(
                deps.llm, day=today, slots=deps.rules.max_major_per_day, candidates=majors,
                goals=goals, mode="keep",
            )  # fmt: skip
            keep = result.task_ids
            deferred = [t for t in majors if t.id not in keep]
            placed = allocate_days(
                deferred, active, today + timedelta(days=1), today + timedelta(days=7), vocab,
                deps.rules.max_major_per_day,
            )  # fmt: skip
            defer = {k: v.isoformat() if v else None for k, v in placed.items()}
            data = {"mode": "trim", "keep_ids": keep, "defer": defer}
        elif not majors and (state.get("inputs") or {}).get("plan_if_empty", True):
            candidates = planning_candidates(active, today, vocab)
            result = await plan_day(
                deps.llm,
                day=today,
                slots=deps.rules.max_major_per_day,
                candidates=candidates,
                goals=goals,
            )
            data = {"mode": "fill", "pick_ids": result.task_ids}
        else:
            return {"plan": {"mode": "as_is", "keep_ids": [t.id for t in majors]}}
        data.update(reasons=result.reasons, note=result.note, source=result.source)
        return {"plan": data, "warnings": result.warnings, "llm": result.usage}

    async def propose(state: MorningState) -> dict[str, Any]:
        today = date.fromisoformat(state["today"])
        plan_ = state["plan"]
        builder = ProposalBuilder(deps, deps.resolver(today))
        warnings: list[str] = []
        if plan_["mode"] == "trim":
            count = len(plan_["keep_ids"]) + len(plan_["defer"])
            for task_id, day in plan_["defer"].items():
                if day is None:
                    title = state["titles"].get(task_id, task_id)
                    warnings.append(f"No free slot in the next 7 days for {title!r}; left as is.")
                    continue
                await builder.set_due(
                    task_id, date.fromisoformat(day), "reschedule",
                    reason=f"Today has {count} major tasks and the limit is 3; this is the "
                    "first day with a free slot.",
                    rule_ids=("R1",), title=state["titles"].get(task_id),
                )  # fmt: skip
        elif plan_["mode"] == "fill":
            for task_id in plan_["pick_ids"]:
                await builder.set_due(
                    task_id, today, "reschedule",
                    reason=plan_["reasons"].get(task_id) or "Planned for today.",
                    rule_ids=("R1",), title=state["titles"].get(task_id),
                )  # fmt: skip
        ids = await builder.commit(
            run_id=run_uuid(state), ttl=timedelta(hours=24), actor=f"agent:{NAME}"
        )
        return {"proposal_ids": [str(i) for i in ids], "warnings": warnings + builder.notes()}

    async def brief(state: MorningState) -> dict[str, Any]:
        today = date.fromisoformat(state["today"])
        active = load_tasks(state["tasks"])
        titles = state.get("titles") or {}
        plan_ = state.get("plan") or {}
        late = overdue(active, today, vocab)
        upcoming = fallback_order(
            [t for t in active if is_open(t, vocab) and t.due
             and today < t.due <= today + timedelta(days=7)],
            vocab,
        )  # fmt: skip
        inbox = [t for t in active if vocab.status_key(t) == "inbox"]
        observations = []
        if late:
            observations.append(f"{len(late)} overdue tasks wait for the weekly review (R6).")
        if state.get("missing_scorecards"):
            days = ", ".join(
                date.fromisoformat(d).strftime("%d %b") for d in state["missing_scorecards"]
            )
            observations.append(f"No scorecard for {days}.")
        if inbox:
            observations.append(f"{len(inbox)} tasks in the Inbox.")
        observations.append(f"{state.get('done_this_week', 0)} tasks done so far this week.")
        decisions = state.get("decisions") or {}
        summary = {
            "workflow": NAME,
            "day": state["today"],
            "priorities": [titles.get(i, i) for i in plan_.get("keep_ids") or []],
            "proposed_for_today": [titles.get(i, i) for i in plan_.get("pick_ids") or []],
            "proposed_to_defer": {
                titles.get(k, k): v for k, v in (plan_.get("defer") or {}).items()
            },
            "planned_by": plan_.get("source"),
            "note": plan_.get("note") or "",
            "next_7_days": [{"title": t.title, "due": t.due.isoformat()} for t in upcoming[:10]],
            "observations": observations,
            "proposals": {
                "approved": len(decisions.get("approved") or []),
                "rejected": len(decisions.get("rejected") or []),
                "errors": decisions.get("errors") or {},
            },
            "execution": outcome_counts(state.get("results") or []),
            "notes": [n for n in [execution_note(state, deps.writes_enabled)] if n],
            "warnings": state.get("warnings") or [],
        }
        return {"summary": summary}

    graph = StateGraph(MorningState)
    for node in (load, plan, propose, brief):
        graph.add_node(node.__name__, node)
    graph.add_edge(START, "load")
    graph.add_edge("load", "plan")
    graph.add_edge("plan", "propose")
    add_approval_flow(graph, deps, NAME, after="propose", then="brief")
    graph.add_edge("brief", END)
    return graph
