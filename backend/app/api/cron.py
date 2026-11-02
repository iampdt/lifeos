"""Scheduled jobs, called by GitHub Actions (decision D8): `POST /api/v1/internal/cron/{job}`.

There is no in-process scheduler, because the free web tier sleeps when idle. Each call is
authenticated by an HMAC-SHA256 signature instead of a session:

    X-LifeOS-Timestamp: <unix seconds>
    X-LifeOS-Signature: hex(HMAC-SHA256(LIFEOS_CRON_SECRET, f"{timestamp}.{job}.{body}"))

The timestamp must be within 5 minutes, and the signature covers the job name, so a captured
request can't be replayed later or reused for another job. A workflow job runs for the owner
account (per-account schedules come with multi-user step 9.6), at most once per local day
(`cron_runs`), so a retried or duplicated call is harmless. Expiring proposals covers every
account and is idempotent, so it simply runs on each call.
"""

from __future__ import annotations

import hashlib
import hmac
import time
import uuid
from datetime import date
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Header, Request
from sqlalchemy import delete, update
from sqlalchemy.dialects.postgresql import insert

from app.api.errors import ApiError
from app.api.security import services
from app.db.models import CronRun
from app.execution.proposals import expire_due

router = APIRouter()

WORKFLOW_JOBS = frozenset({"morning_briefing", "evening_checkin", "weekly_review"})
JOBS = WORKFLOW_JOBS | {"expire_proposals"}
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
    async with deps.session_factory() as db, db.begin():
        claimed = await db.scalar(
            insert(CronRun)
            .values(user_id=deps.user_id, job=job, local_date=today)
            .on_conflict_do_nothing()
            .returning(CronRun.job)
        )
    if claimed is None:
        return {"job": job, "date": today.isoformat(), "status": "already_ran"}

    view = await context.runner.start(job, trigger="cron")
    async with deps.session_factory() as db, db.begin():
        slot = _slot(deps.user_id, job, today)
        if view.status == "failed":
            # Free the slot so a retry later the same day can try again.
            await db.execute(delete(CronRun).where(*slot))
        else:
            await db.execute(
                update(CronRun).where(*slot).values(run_id=view.id, result={"status": view.status})
            )
    if view.status == "failed":
        raise ApiError(502, "run_failed", (view.error or {}).get("message", "run failed"), True)
    return {"job": job, "date": today.isoformat(), "status": view.status, "run_id": view.id}


def _slot(user_id: uuid.UUID, job: str, day: date) -> tuple:
    return (CronRun.user_id == user_id, CronRun.job == job, CronRun.local_date == day)
