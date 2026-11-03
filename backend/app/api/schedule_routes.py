"""Catching up on routines when an account opens the app (multi-user §Scheduling).

The hourly tick comes from GitHub Actions, whose schedules can run hours late. When the web app
loads, it asks for any of the account's routines that are due but haven't run today; they start
then, claimed exactly as the tick claims them, so nothing runs twice.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from app.api.cron import _claim, _claimed, _run, _schedules
from app.api.errors import ApiError
from app.api.security import CurrentUser, services
from app.workflows.schedule import due_routines

router = APIRouter()

MAX_PER_VISIT = 2


@router.post("/schedule/catch-up")
async def catch_up(request: Request, principal: CurrentUser) -> dict[str, Any]:
    svc = services(request)
    if svc.settings.lifeos_demo_mode:
        raise ApiError(404, "http_error", "Not Found")  # the demo has no schedule
    if not svc.oauth_limiter.allow(f"catch-up:{principal.user_id}"):
        return {"started": []}
    now = svc.clock()
    started: list[dict[str, Any]] = []
    for user_id, is_owner, schedule, _ in await _schedules(svc, principal.user_id):
        for routine in due_routines(schedule, now):
            if len(started) >= MAX_PER_VISIT:
                break
            if await _claimed(svc, user_id, routine.job, routine.local_date):
                continue
            context = await svc.contexts.for_user(user_id, is_owner=is_owner)
            if context is None or not await _claim(svc, user_id, routine.job, routine.local_date):
                continue
            view = await _run(svc, context, routine.job, routine.local_date)
            started.append({"job": routine.job, "status": view.status, "run_id": view.id})
    return {"started": started}
