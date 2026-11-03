"""Monthly review.

load → draft (AI narrative) → ⏸ note → propose → ⏸ approve → execute → summarize

It reviews last month when run in the first week of a month (as the schedule does, on the 1st)
and the current month otherwise, or `inputs.month_of`. It reads the month's tasks, scorecard,
goals and weekly reviews, closes the month's page with its review and creates next month's page
where the workspace has monthly pages. Tasks and goals are never changed.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import date, timedelta
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.agents.monthly import draft_month_review, render_month_review
from app.domain.models import Goal
from app.execution.actions import ActionKind, ClosePeriod
from app.services.calendar import month_range, month_title
from app.services.periods import PeriodResolver
from app.services.rules import HabitSummary, habit_summary, is_open, week_metrics
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
from app.workflows.weekly import _metrics_dict, _metrics_obj

NAME = "monthly_review"


class MonthlyState(BaseState, total=False):
    month: dict[str, Any]  # start, end, label, days
    metrics: dict[str, Any]
    habits: dict[str, Any]
    done_tasks: list[dict[str, Any]]
    slipped: list[dict[str, Any]]
    goals: list[dict[str, Any]]
    weekly_reviews: list[str]
    month_page: dict[str, Any] | None
    review: dict[str, Any]
    note: str | None


def month_to_review(today: date, month_of: str | None = None) -> tuple[date, date]:
    if month_of:
        return month_range(date.fromisoformat(month_of))
    anchor = today - timedelta(days=7) if today.day <= 7 else today
    return month_range(anchor)


def build_graph(deps: WorkflowDeps) -> StateGraph:
    ws, vocab = deps.workspace, deps.vocab

    async def load(state: MonthlyState) -> dict[str, Any]:
        today = deps.today()
        start, end = month_to_review(today, (state.get("inputs") or {}).get("month_of"))
        days = (end - start).days + 1
        in_month = await ws.tasks.due_between(start, end)
        done = [t for t in in_month if vocab.status_key(t) == "done"]
        slipped = [t for t in in_month if is_open(t, vocab) and vocab.status_key(t) != "inbox"]
        rows = await ws.habits.all() if ws.habits else []
        before_start, _ = month_range(start - timedelta(days=1))
        this_month = habit_summary(rows, start, end)
        last_month = habit_summary(rows, before_start, start - timedelta(days=1))
        goals = await ws.goals.active() if ws.goals else []
        reviews: list[str] = []
        if ws.weeks:
            for page in await ws.weeks.all():
                if page.start and start <= page.start <= end and page.review.strip():
                    reviews.append(page.review)
        month_page = None
        if ws.months:
            pages = await ws.months.covering(start)
            if pages:
                month_page = {"id": pages[0].id, "review": pages[0].review}
        return {
            "today": today.isoformat(),
            "month": {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "label": month_title(start),
                "days": days,
            },  # fmt: skip
            "metrics": _metrics_dict(week_metrics(in_month, vocab)),
            "habits": {"this_month": asdict(this_month), "last_month": asdict(last_month)},
            "done_tasks": dump_tasks(done),
            "slipped": dump_tasks(slipped),
            "goals": [g.model_dump(mode="json") for g in goals],
            "weekly_reviews": reviews,
            "month_page": month_page,
            "titles": {t.id: t.title for t in in_month},
        }

    async def draft(state: MonthlyState) -> dict[str, Any]:
        result = await draft_month_review(
            deps.llm,
            month_label=state["month"]["label"],
            days=state["month"]["days"],
            metrics=_metrics_obj(state["metrics"]),
            habits=HabitSummary.from_dict(state["habits"]["this_month"]),
            previous_habits=HabitSummary.from_dict(state["habits"]["last_month"]),
            scorecard=deps.scorecard,
            done=load_tasks(state["done_tasks"]),
            slipped=load_tasks(state["slipped"]),
            goals=[Goal.model_validate(g) for g in state.get("goals") or []],
            weekly_reviews=state.get("weekly_reviews") or [],
            memory=await deps.memory(),
        )
        review = {k: v for k, v in result.as_dict().items() if k not in ("warnings", "usage")}
        return {"review": review, "warnings": result.warnings, "llm": result.usage}

    async def ask(state: MonthlyState) -> dict[str, Any]:
        goal_titles = {g["id"]: g["title"] for g in state.get("goals") or []}
        review = state["review"]
        answer = interrupt(
            {
                "type": "month_review",
                "workflow": NAME,
                "month": state["month"],
                "metrics": state["metrics"],
                "habits": state["habits"],
                "scorecard": deps.scorecard.describe(),
                "review": {
                    **{k: review.get(k) for k in
                       ("summary", "wins", "misses", "patterns", "focus", "source")},
                    "goals": [{"title": goal_titles.get(g, "Goal"), "note": n}
                              for g, n in (review.get("goal_notes") or {}).items()],
                },
                "closes_page": bool(state.get("month_page")) and _can_close(deps),
                "accepts": {"note": "optional; your own words, added to the month's Review"},
            }
        )  # fmt: skip
        answer = answer if isinstance(answer, dict) else {}
        note = str(answer.get("note") or "").strip()[:1000] or None
        return {"note": note}

    async def propose(state: MonthlyState) -> dict[str, Any]:
        today = date.fromisoformat(state["today"])
        resolver = PeriodResolver(None, ws.months, today)
        builder = ProposalBuilder(deps, resolver)
        warnings: list[str] = []
        page = state.get("month_page")
        if page and _can_close(deps):
            goal_titles = {g["id"]: g["title"] for g in state.get("goals") or []}
            text = render_month_review(state["month"]["label"], state["review"],
                                       _metrics_obj(state["metrics"]), goal_titles,
                                       state.get("note"))  # fmt: skip
            if page.get("review"):
                # Keep what the owner already wrote; LifeOS's review goes underneath.
                text = f"{page['review'].rstrip()}\n\n{text}"
            await builder.add(
                ActionKind.MONTH_CLOSE,
                ClosePeriod(page_id=page["id"], review=text[:6000]),
                reason="Close the month with its review.",
                title=state["month"]["label"],
            )
        elif ws.months and not page:
            warnings.append("This month has no monthly page, so there is nothing to close.")
        if ws.months:
            next_start = date.fromisoformat(state["month"]["end"]) + timedelta(days=1)
            await builder.ensure_period(
                await resolver.resolve("month", next_start), "Next month's page."
            )
        ids = await builder.commit(
            run_id=run_uuid(state), ttl=timedelta(days=7), actor=f"agent:{NAME}"
        )
        return {"proposal_ids": [str(i) for i in ids], "warnings": warnings + builder.notes()}

    async def summarize(state: MonthlyState) -> dict[str, Any]:
        review = state.get("review") or {}
        decisions = state.get("decisions") or {}
        summary = {
            "workflow": NAME,
            "month": state.get("month"),
            "metrics": state.get("metrics"),
            "review": {k: review.get(k) for k in
                       ("summary", "wins", "misses", "patterns", "focus", "source")},
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

    graph = StateGraph(MonthlyState)
    for node in (load, draft, ask, propose, summarize):
        graph.add_node(node.__name__, node)
    graph.add_edge(START, "load")
    graph.add_edge("load", "draft")
    graph.add_edge("draft", "ask")
    graph.add_edge("ask", "propose")
    add_approval_flow(graph, deps, NAME, after="propose", then="summarize")
    graph.add_edge("summarize", END)
    return graph


def _can_close(deps: WorkflowDeps) -> bool:
    months = deps.specs.get("months")
    return months is not None and (months.has("review") or months.has_value("status", "closed"))
