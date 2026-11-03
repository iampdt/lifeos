"""Usage and latency reports from an account's own runs (`agent_runs`).

Per workflow: how many runs, how they ended, how long they took, how many model calls and
tokens they used, and the model's latency per run (median and 95th percentile). Per day: runs
and tokens. Everything is counted from what the runner already records; nothing new is stored.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from statistics import median
from typing import Any

from fastapi import APIRouter, Query, Request
from sqlalchemy import select

from app.api.security import Context, services
from app.db.models import AgentRun

router = APIRouter()


def percentile(values: list[int], share: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(share * (len(ordered) - 1)))]


@router.get("/reports/usage")
async def usage(
    request: Request, ctx: Context, days: int = Query(30, ge=1, le=365)
) -> dict[str, Any]:
    svc = services(request)
    since = svc.clock() - timedelta(days=days)
    async with ctx.deps.session_factory() as db:
        runs = list(
            await db.scalars(
                select(AgentRun).where(
                    AgentRun.user_id == ctx.deps.user_id, AgentRun.started_at >= since
                )
            )
        )
    by_workflow: dict[str, list[AgentRun]] = defaultdict(list)
    by_day: dict[str, dict[str, int]] = defaultdict(lambda: {"runs": 0, "tokens": 0})
    models: dict[str, int] = defaultdict(int)
    for run in runs:
        by_workflow[run.workflow].append(run)
        day = run.started_at.astimezone(ctx.deps.tz).date().isoformat()
        by_day[day]["runs"] += 1
        by_day[day]["tokens"] += run.input_tokens + run.output_tokens
        if run.model:
            models[run.model] += 1
    return {
        "days": days,
        "totals": _summary(runs),
        "workflows": {name: _summary(items) for name, items in sorted(by_workflow.items())},
        "by_day": dict(sorted(by_day.items())),
        "models": dict(sorted(models.items(), key=lambda kv: -kv[1])),
    }


def _summary(runs: list[AgentRun]) -> dict[str, Any]:
    statuses: dict[str, int] = defaultdict(int)
    for run in runs:
        statuses[run.status] += 1
    durations = [
        int((r.finished_at - r.started_at).total_seconds() * 1000)
        for r in runs
        if r.finished_at and r.status == "completed"
    ]
    latencies = [r.llm_latency_ms for r in runs if r.llm_latency_ms]
    calls = sum(int((r.output or {}).get("llm_calls", 0)) for r in runs)
    ended = statuses["completed"] + statuses["failed"]
    return {
        "runs": len(runs),
        "statuses": dict(statuses),
        "success_rate": statuses["completed"] / ended if ended else None,
        "llm_calls": calls,
        "input_tokens": sum(r.input_tokens for r in runs),
        "output_tokens": sum(r.output_tokens for r in runs),
        "llm_latency_ms": {
            "p50": int(median(latencies)) if latencies else None,
            "p95": percentile(latencies, 0.95),
        },  # fmt: skip
        # Wall time of finished runs, including any wait for the owner's answer.
        "duration_ms": {"p50": int(median(durations)) if durations else None},
    }
