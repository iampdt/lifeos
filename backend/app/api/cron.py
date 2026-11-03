"""Scheduled jobs, called by GitHub Actions (decision D8): `POST /api/v1/internal/cron/{job}`.

There is no in-process scheduler, because the free web tier sleeps when idle. Each call is
authenticated by an HMAC-SHA256 signature instead of a session:

    X-LifeOS-Timestamp: <unix seconds>
    X-LifeOS-Signature: hex(HMAC-SHA256(LIFEOS_CRON_SECRET, f"{timestamp}.{job}.{body}"))

The timestamp must be within 5 minutes, and the signature covers the job name, so a captured
request can't be replayed later or reused for another job.

`tick` is the scheduler (multi-user §Scheduling): called every hour, it expires stale proposals,
runs each account's daily drift check, and starts the routines that are due in each account's
own timezone (`workflows/schedule.py`), the oldest first, up to a cap per tick. A routine runs at
most once per account and local day (`cron_runs`), so retried or overlapping ticks are
harmless; a failed run frees its slot for the next tick. The named jobs (`morning_briefing`, …)
still run one routine for the owner on demand.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
import uuid
from datetime import date
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Header, Request
from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert

from app.api.errors import ApiError
from app.api.security import services
from app.db.models import CronRun, NotionConnection, User, Workspace
from app.execution.proposals import expire_due
from app.workflows.contexts import UserContext
from app.workflows.runner import RunView
from app.workflows.schedule import Schedule, due_routines, parse_time
from app.workspaces.drift import check_drift

logger = logging.getLogger("lifeos.cron")
router = APIRouter()

WORKFLOW_JOBS = frozenset(
    {"morning_briefing", "evening_checkin", "weekly_review", "monthly_review"}
)
JOBS = WORKFLOW_JOBS | {"expire_proposals", "tick"}
MAX_SKEW_SECONDS = 300


def sign(secret: str, timestamp: str, job: str, body: bytes) -> str:
    message = f"{timestamp}.{job}.".encode() + body
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


@router.post("/internal/cron/{job}")
async def run_job(
    job: str,
    request: Request,
    x_lifeos_timestamp: str = Header(""),
    x_lifeos_signature: str = Header(""),
) -> dict[str, Any]:
    svc = services(request)
    secret = svc.settings.lifeos_cron_secret
    if secret is None:
        raise ApiError(503, "cron_not_configured", "LIFEOS_CRON_SECRET is not set")
    try:
        skew = abs(time.time() - int(x_lifeos_timestamp))
    except ValueError:
        skew = float("inf")
    body = await request.body()
    expected = sign(secret.get_secret_value().strip(), x_lifeos_timestamp, job, body)
    if skew > MAX_SKEW_SECONDS or not hmac.compare_digest(expected, x_lifeos_signature):
        raise ApiError(401, "bad_signature", "missing, stale or invalid cron signature")
    if job not in JOBS:
        raise ApiError(404, "unknown_job", f"no cron job {job!r}")

    if job == "tick":
        return await _tick(svc)
    if job == "expire_proposals":
        today = svc.clock().astimezone(ZoneInfo(svc.settings.timezone)).date()
        async with svc.session_factory() as db, db.begin():
            expired = await expire_due(db, clock=svc.clock)
        return {"job": job, "date": today.isoformat(), "status": "done", "expired": expired}

    context = await svc.owner_context()
    if context is None:
        return {"job": job, "status": "skipped", "reason": "no owner account"}
    deps = context.deps
    today = deps.today()
    if not await _claim(svc, deps.user_id, job, today):
        return {"job": job, "date": today.isoformat(), "status": "already_ran"}
    view = await _run(svc, context, job, today)
    if view.status == "failed":
        raise ApiError(502, "run_failed", (view.error or {}).get("message", "run failed"), True)
    return {"job": job, "date": today.isoformat(), "status": view.status, "run_id": view.id}


# --- the scheduler -------------------------------------------------------------------------


async def _tick(svc) -> dict[str, Any]:
    started = time.monotonic()
    now = svc.clock()
    async with svc.session_factory() as db, db.begin():
        expired = await expire_due(db, clock=svc.clock)
    schedules = await _schedules(svc)

    checked = 0
    if svc.connections is not None:
        for user_id, _, schedule, saved in schedules:
            day = now.astimezone(schedule.tz).date()
            if not saved or not await _claim(svc, user_id, "drift_check", day):
                continue
            try:
                paused = await check_drift(svc.session_factory, svc.connections, user_id, svc.clock)
            except Exception:  # Notion unavailable or the connection lost: try on the next tick
                logger.exception("drift check failed for %s", user_id)
                await _release(svc, user_id, "drift_check", day)
                continue
            await _record(svc, user_id, "drift_check", day, result={"paused": paused})
            await svc.contexts.forget(user_id)
            checked += 1

    due = sorted(
        (
            (user_id, is_owner, d)
            for user_id, is_owner, s, _ in schedules
            for d in due_routines(s, now)
        ),
        key=lambda item: item[2].since,
    )
    ran: list[dict[str, Any]] = []
    waiting = 0
    for user_id, is_owner, routine in due:
        if await _claimed(svc, user_id, routine.job, routine.local_date):
            continue
        out_of_time = time.monotonic() - started > svc.settings.lifeos_tick_seconds
        if len(ran) >= svc.settings.lifeos_tick_max_runs or out_of_time:
            waiting += 1  # the next tick picks it up while it's still in its window
            continue
        context = await svc.contexts.for_user(user_id, is_owner=is_owner)
        if context is None or not await _claim(svc, user_id, routine.job, routine.local_date):
            continue
        view = await _run(svc, context, routine.job, routine.local_date)
        ran.append({"user_id": str(user_id), "job": routine.job, "status": view.status,
                    "run_id": view.id})  # fmt: skip
    return {"job": "tick", "status": "done", "expired": expired, "drift_checked": checked,
            "ran": ran, "waiting": waiting}  # fmt: skip


async def _schedules(
    svc, user_id: uuid.UUID | None = None
) -> list[tuple[uuid.UUID, bool, Schedule, bool]]:
    """(account, is owner, schedule, saved workspace?) for every connected account with a saved
    workspace, and for the owner: its saved workspace's schedule once it has moved to its own
    connection, the settings' times on the environment's workspace until then. With `user_id`,
    only that account's."""
    s = svc.settings
    out: list[tuple[uuid.UUID, bool, Schedule, bool]] = []
    query = (
        select(Workspace)
        .join(NotionConnection, NotionConnection.user_id == Workspace.user_id)
        .where(NotionConnection.status == "active")
    )
    if user_id is not None:
        query = query.where(Workspace.user_id == user_id)
    async with svc.session_factory() as db:
        owner_id = await db.scalar(select(User.id).where(User.is_owner))
        rows = (await db.scalars(query)).all()
    if user_id is not None and owner_id != user_id:
        owner_id = None  # only the requested account
    for row in rows:
        out.append((row.user_id, row.user_id == owner_id, Schedule(
            ZoneInfo(row.timezone), parse_time(row.morning_time), parse_time(row.evening_time),
            row.review_weekday, parse_time(row.review_time), row.schedule_enabled,
        ), True))  # fmt: skip
    if owner_id is not None and all(user_id != owner_id for user_id, *_ in out):
        out.append((owner_id, True, Schedule(
            ZoneInfo(s.timezone), parse_time(s.lifeos_morning_time),
            parse_time(s.lifeos_evening_time), s.lifeos_review_weekday,
            parse_time(s.lifeos_review_time),
        ), False))  # fmt: skip
    return out


# --- claiming a slot -----------------------------------------------------------------------


def _slot(user_id: uuid.UUID, job: str, day: date) -> tuple:
    return (CronRun.user_id == user_id, CronRun.job == job, CronRun.local_date == day)


async def _claim(svc, user_id: uuid.UUID, job: str, day: date) -> bool:
    async with svc.session_factory() as db, db.begin():
        claimed = await db.scalar(
            insert(CronRun)
            .values(user_id=user_id, job=job, local_date=day)
            .on_conflict_do_nothing()
            .returning(CronRun.job)
        )
    return claimed is not None


async def _claimed(svc, user_id: uuid.UUID, job: str, day: date) -> bool:
    async with svc.session_factory() as db:
        return await db.scalar(select(CronRun.job).where(*_slot(user_id, job, day))) is not None


async def _release(svc, user_id: uuid.UUID, job: str, day: date) -> None:
    async with svc.session_factory() as db, db.begin():
        await db.execute(delete(CronRun).where(*_slot(user_id, job, day)))


async def _record(svc, user_id, job, day, *, run_id: str | None = None, result: dict) -> None:
    async with svc.session_factory() as db, db.begin():
        await db.execute(
            update(CronRun).where(*_slot(user_id, job, day)).values(run_id=run_id, result=result)
        )


async def _run(svc, context: UserContext, job: str, day: date) -> RunView:
    view = await context.runner.start(job, trigger="cron")
    if view.status == "failed":
        await _release(svc, context.deps.user_id, job, day)  # a later call can try again
    else:
        await _record(svc, context.deps.user_id, job, day, run_id=view.id,
                      result={"status": view.status})  # fmt: skip
    return view
