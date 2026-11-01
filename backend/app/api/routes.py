"""`/api/v1` routes. Everything except `/health` needs `Authorization: Bearer <LIFEOS_API_TOKEN>`.

Session login for the web app comes with the frontend (Phase 6); until then a single owner
token protects the API, and an unset token refuses every request (fail closed).
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import asdict
from typing import TYPE_CHECKING, Any, Literal

from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select, text

from app.api.errors import ApiError
from app.db.models import ActionProposal

if TYPE_CHECKING:
    from app.api.main import Services

router = APIRouter()


def services(request: Request) -> Services:
    return request.app.state.services


async def require_token(request: Request, authorization: str | None = Header(None)) -> None:
    expected = services(request).settings.lifeos_api_token
    if expected is None:
        raise ApiError(503, "auth_not_configured", "LIFEOS_API_TOKEN is not set")
    scheme, _, token = (authorization or "").partition(" ")
    valid = scheme.lower() == "bearer" and secrets.compare_digest(
        token.encode(), expected.get_secret_value().encode()
    )
    if not valid:
        raise ApiError(401, "unauthorized", "missing or invalid bearer token")


protected = APIRouter(dependencies=[Depends(require_token)])


class StartRun(BaseModel):
    inputs: dict[str, Any] = Field(default_factory=dict)


class ResumeRun(BaseModel):
    answer: Any = Field(description="The reply to the run's `waiting_for` request")


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@protected.get("/health/ready")
async def ready(request: Request) -> dict[str, Any]:
    svc = services(request)
    async with svc.deps.session_factory() as session:
        await session.execute(text("select 1"))
    return {
        "database": "ok",
        "notion_profile": svc.settings.notion_profile,
        "writes_enabled": svc.deps.writes_enabled,
        "llm_configured": svc.deps.llm.provider is not None,
    }


@protected.post("/workflows/{name}/runs", status_code=201)
async def start_run(name: str, body: StartRun, request: Request) -> dict[str, Any]:
    view = await services(request).runner.start(name, trigger="manual", inputs=body.inputs)
    return asdict(view)


@protected.get("/runs/{run_id}")
async def get_run(run_id: uuid.UUID, request: Request) -> dict[str, Any]:
    return asdict(await services(request).runner.get(run_id))


@protected.post("/runs/{run_id}/resume")
async def resume_run(run_id: uuid.UUID, body: ResumeRun, request: Request) -> dict[str, Any]:
    return asdict(await services(request).runner.resume(run_id, body.answer))


@protected.get("/proposals")
async def list_proposals(
    request: Request,
    status: str = "pending",
    limit: int = Query(50, ge=1, le=200),
) -> list[dict[str, Any]]:
    async with services(request).deps.session_factory() as session:
        rows = await session.scalars(
            select(ActionProposal)
            .where(ActionProposal.status == status)
            .order_by(ActionProposal.created_at.desc())
            .limit(limit)
        )
        return [
            {
                "id": str(p.id),
                "run_id": str(p.run_id) if p.run_id else None,
                "kind": p.kind,
                "status": p.status,
                "importance": p.importance,
                "target": {"data_source": p.target_data_source, "page_id": p.target_page_id},
                "before": p.before_snapshot,
                "payload": p.effective_payload,
                "reason": p.reason,
                "rule_ids": list(p.rule_ids or []),
                "depends_on": [str(d) for d in p.depends_on or []],
                "created_at": p.created_at.isoformat(),
                "expires_at": p.expires_at.isoformat(),
            }
            for p in rows
        ]


@protected.get("/tasks")
async def list_tasks(
    request: Request,
    view: Literal["active", "today", "overdue", "inbox", "next7"] = "active",
) -> list[dict[str, Any]]:
    deps = services(request).deps
    tasks = deps.workspace.tasks
    today = deps.today()
    match view:
        case "active":
            result = await tasks.active()
        case "today":
            result = await tasks.today(today)
        case "overdue":
            result = await tasks.overdue(today)
        case "inbox":
            result = await tasks.inbox()
        case "next7":
            result = await tasks.next_7_days(today)
    return [t.model_dump(mode="json") for t in result]


router.include_router(protected)
