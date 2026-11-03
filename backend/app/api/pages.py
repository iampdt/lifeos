"""Read-only views behind the web app's Goals and History pages.

Goals and reading come straight from Notion through the account's mapping (an unmapped module is
reported as unavailable, not as an error). History pairs each day's morning briefing with its
evening check-in from the account's own runs.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Query, Request
from sqlalchemy import select

from app.api.security import Context, services
from app.db.models import AgentRun
from app.notion.mapping import DataSourceSpec

router = APIRouter()

GOAL_ORDER = ("active", "planned", "done", "dropped")
BOOK_ORDER = ("reading", "want", "finished", "abandoned")
DAILY = {"morning_briefing": "briefing", "evening_checkin": "checkin"}


def state_of(spec: DataSourceSpec, field: str, value: str | None) -> str | None:
    """The semantic key ("active", "reading", …) of a status option, or None."""
    if value is None or not spec.has(field):
        return None
    for key, name in spec.prop(field).values.items():
        if name == value:
            return key
    return None


def share(done: float | None, total: float | None) -> float | None:
    if done is None or not total or total <= 0:
        return None
    return max(0.0, min(1.0, done / total))


def _rank(order: tuple[str, ...], key: str | None) -> int:
    return order.index(key) if key in order else len(order)


@router.get("/goals")
async def goals(ctx: Context) -> dict[str, Any]:
    deps = ctx.deps
    repo = deps.workspace.goals
    if repo is None:
        return {"available": False, "goals": []}
    items = await repo.all()
    open_tasks = await deps.workspace.tasks.active()
    linked: dict[str, list[dict[str, Any]]] = {}
    for task in sorted(open_tasks, key=lambda t: (t.due is None, t.due)):
        for goal_id in task.goal_ids:
            linked.setdefault(goal_id, []).append(
                {"id": task.id, "title": task.title, "status": task.status,
                 "due": task.due.isoformat() if task.due else None, "url": task.url}
            )  # fmt: skip
    rows = []
    for goal in items:
        state = state_of(repo.spec, "status", goal.status)
        rows.append({
            **goal.model_dump(mode="json"),
            "state": state,
            "progress": share(goal.current, goal.target),
            "open_tasks": linked.get(goal.id, []),
        })  # fmt: skip
    rows.sort(key=lambda g: (_rank(GOAL_ORDER, g["state"]), g["deadline"] is None,
                             g["deadline"] or "", g["title"].lower()))  # fmt: skip
    return {"available": True, "tasks_linked": deps.specs["tasks"].has("goal"), "goals": rows}


@router.get("/reading")
async def reading(ctx: Context) -> dict[str, Any]:
    repo = ctx.deps.workspace.books
    if repo is None:
        return {"available": False, "books": []}
    rows = []
    for book in await repo.queue():
        rows.append({
            **book.model_dump(mode="json"),
            "state": state_of(repo.spec, "status", book.status),
            "progress": share(book.pages_read, book.pages),
        })  # fmt: skip
    rows.sort(key=lambda b: (_rank(BOOK_ORDER, b["state"]),
                             b["up_next"] if b["up_next"] is not None else float("inf"),
                             b["title"].lower()))  # fmt: skip
    return {"available": True, "books": rows}


@router.get("/history")
async def history(
    request: Request, ctx: Context, days: int = Query(14, ge=1, le=90)
) -> list[dict[str, Any]]:
    """Newest day first: the latest morning briefing and evening check-in started for each day."""
    svc = services(request)
    tz = ctx.deps.tz
    since = svc.clock() - timedelta(days=days)
    async with ctx.deps.session_factory() as db:
        runs = list(
            await db.scalars(
                select(AgentRun)
                .where(
                    AgentRun.user_id == ctx.deps.user_id,
                    AgentRun.workflow.in_(DAILY),
                    AgentRun.started_at >= since,
                )
                .order_by(AgentRun.started_at)
            )
        )
    by_day: dict[str, dict[str, Any]] = {}
    for run in runs:
        summary = (run.output or {}).get("summary")
        # The workflow's own day (its timezone at the time); otherwise the day it started.
        day = (summary or {}).get("day") or run.started_at.astimezone(tz).date().isoformat()
        entry = by_day.setdefault(day, {"day": day, "briefing": None, "checkin": None})
        entry[DAILY[run.workflow]] = {
            "id": str(run.id),
            "status": run.status,
            "started_at": run.started_at.isoformat(),
            "summary": summary,
        }  # later runs replace earlier ones
    return sorted(by_day.values(), key=lambda d: d["day"], reverse=True)
