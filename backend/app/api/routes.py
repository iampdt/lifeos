"""`/api/v1` routes. Everything except `/health` and `/auth/login` needs the owner (see
`security.py`): a browser session with CSRF header, or the bearer token."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import asdict
from datetime import timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, text

from app.agents.chat import ChatAgent
from app.api.errors import ApiError
from app.api.security import Principal, require_owner, services
from app.auth.sessions import SESSION_COOKIE, SessionService
from app.db.models import ActionProposal, AgentRun
from app.demo.setup import demo_user, reset_demo
from app.notion.errors import NotionError
from app.notion.schema import check_data_source
from app.permissions.policy import DEFAULT_MODES
from app.workflows.common import apply_decisions, proposal_cards
from app.workflows.runner import WAITING

router = APIRouter()
protected = APIRouter(dependencies=[Depends(require_owner)])


class LoginBody(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=1024)


class StartRun(BaseModel):
    inputs: dict[str, Any] = Field(default_factory=dict)


class ResumeRun(BaseModel):
    answer: Any = Field(description="The reply to the run's `waiting_for` request")


class Decision(BaseModel):
    edits: dict[str, Any] | None = None
    note: str | None = Field(default=None, max_length=500)


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=4000)


class ChatBody(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    history: list[ChatMessage] = Field(default_factory=list, max_length=20)


class BatchDecision(BaseModel):
    ids: list[uuid.UUID] = Field(min_length=1, max_length=50)
    note: str | None = Field(default=None, max_length=500)


# --- health and auth -------------------------------------------------------------------


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/meta")
async def meta(request: Request) -> dict[str, Any]:
    """Public facts the sign-in page needs before anyone is signed in."""
    return {"demo": services(request).settings.lifeos_demo_mode}


@router.post("/auth/demo")
async def demo_login(request: Request, response: Response) -> dict[str, Any]:
    """Demo only: a session on the shared demo account, no password."""
    svc = services(request)
    if not svc.settings.lifeos_demo_mode:
        raise ApiError(404, "http_error", "Not Found")
    if not svc.demo_limiter.allow("demo"):
        raise ApiError(429, "rate_limited", "too many demo sessions; try again in a minute", True)
    ttl = timedelta(days=1)
    user = await demo_user(svc.deps.session_factory)
    async with svc.deps.session_factory() as db, db.begin():
        token, session = await SessionService(db, ttl=ttl, clock=svc.deps.clock).start(user)
    _set_session_cookie(response, svc, token, ttl)
    return {"email": user.email, "csrf_token": session.csrf_token}


def _set_session_cookie(response: Response, svc, token: str, ttl: timedelta) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(ttl.total_seconds()),
        httponly=True,
        secure=svc.settings.lifeos_cookie_secure,
        samesite="lax",
        path="/",
    )


@router.post("/auth/login")
async def login(body: LoginBody, request: Request, response: Response) -> dict[str, Any]:
    svc = services(request)
    if not svc.login_limiter.allow("login"):
        raise ApiError(429, "rate_limited", "too many sign-in attempts; wait a minute", True)
    ttl = timedelta(days=svc.settings.lifeos_session_days)
    async with svc.deps.session_factory() as db, db.begin():
        result = await SessionService(db, ttl=ttl, clock=svc.deps.clock).login(
            body.email, body.password
        )
    if result is None:
        raise ApiError(401, "invalid_credentials", "wrong email or password")
    token, session, user = result
    _set_session_cookie(response, svc, token, ttl)
    return {"email": user.email, "csrf_token": session.csrf_token}


@protected.get("/auth/me")
async def me(principal: Annotated[Principal, Depends(require_owner)]) -> dict[str, Any]:
    return {"via": principal.via, "email": principal.email, "csrf_token": principal.csrf_token}


@protected.post("/auth/logout")
async def logout(request: Request, response: Response) -> dict[str, str]:
    cookie = request.cookies.get(SESSION_COOKIE)
    if cookie:
        svc = services(request)
        async with svc.deps.session_factory() as db, db.begin():
            await SessionService(db, clock=svc.deps.clock).logout(cookie)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"status": "signed_out"}


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


# --- workflow runs -----------------------------------------------------------------------


@protected.post("/workflows/{name}/runs", status_code=201)
async def start_run(name: str, body: StartRun, request: Request) -> dict[str, Any]:
    view = await services(request).runner.start(name, trigger="manual", inputs=body.inputs)
    return asdict(view)


@protected.get("/runs")
async def list_runs(
    request: Request, status: str | None = None, limit: int = Query(20, ge=1, le=100)
) -> list[dict[str, Any]]:
    runs = await services(request).runner.recent(limit=limit, status=status)
    # The list is for overviews; fetch a run to get its full request and summary.
    return [{**asdict(r), "waiting_for": None, "summary": None} for r in runs]


@protected.get("/runs/{run_id}")
async def get_run(run_id: uuid.UUID, request: Request) -> dict[str, Any]:
    return asdict(await services(request).runner.get(run_id))


@protected.post("/runs/{run_id}/resume")
async def resume_run(run_id: uuid.UUID, body: ResumeRun, request: Request) -> dict[str, Any]:
    return asdict(await services(request).runner.resume(run_id, body.answer))


# --- approvals ---------------------------------------------------------------------------


@protected.get("/approvals")
async def approvals(request: Request) -> dict[str, Any]:
    """Everything waiting for the owner: runs paused at their approval step (decided through
    `/runs/{id}/resume`) and standalone proposals, e.g. from chat (decided below)."""
    deps = services(request).deps
    async with deps.session_factory() as session:
        waiting = list(
            await session.scalars(
                select(AgentRun)
                .where(AgentRun.status == WAITING["approval"])
                .order_by(AgentRun.started_at.desc())
            )
        )
        standalone = list(
            await session.scalars(
                select(ActionProposal.id)
                .outerjoin(AgentRun, AgentRun.id == ActionProposal.run_id)
                .where(
                    ActionProposal.status == "pending",
                    (AgentRun.id.is_(None)) | (AgentRun.status != WAITING["approval"]),
                )
                .order_by(ActionProposal.created_at)
            )
        )
    runs = []
    for run in waiting:
        ids = [c["id"] for c in ((run.output or {}).get("waiting_for") or {}).get("proposals", [])]
        runs.append(
            {
                "run_id": str(run.id),
                "workflow": run.workflow,
                "started_at": run.started_at.isoformat(),
                "proposals": await proposal_cards(deps, ids, {}),
            }
        )
    return {
        "runs": runs,
        "standalone": await proposal_cards(deps, [str(i) for i in standalone], {}),
    }


@protected.get("/proposals")
async def list_proposals(
    request: Request,
    status: str = "pending",
    limit: int = Query(50, ge=1, le=200),
) -> list[dict[str, Any]]:
    deps = services(request).deps
    async with deps.session_factory() as session:
        ids = list(
            await session.scalars(
                select(ActionProposal.id)
                .where(ActionProposal.status == status)
                .order_by(ActionProposal.created_at.desc())
                .limit(limit)
            )
        )
    return await proposal_cards(deps, [str(i) for i in ids], {})


async def _decide(request: Request, ids: list[uuid.UUID], answer: dict[str, Any]) -> dict[str, Any]:
    deps = services(request).deps
    async with deps.session_factory() as session:
        owned = await session.scalar(
            select(ActionProposal.id)
            .join(AgentRun, AgentRun.id == ActionProposal.run_id)
            .where(ActionProposal.id.in_(ids), AgentRun.status == WAITING["approval"])
            .limit(1)
        )
    if owned is not None:
        raise ApiError(409, "run_owned", "this proposal belongs to a run waiting for approval")
    str_ids = [str(i) for i in ids]
    decisions = await apply_decisions(deps, str_ids, answer)
    results = []
    if decisions["approved"]:
        executed = await deps.executor("owner").execute_approved(
            [uuid.UUID(i) for i in decisions["approved"]]
        )
        results = [
            {"proposal_id": str(r.proposal_id), "outcome": r.outcome, "page_id": r.page_id,
             "detail": r.detail}
            for r in executed
        ]  # fmt: skip
    return {**decisions, "results": results, "writes_enabled": deps.writes_enabled}


@protected.post("/proposals/{proposal_id}/approve")
async def approve(proposal_id: uuid.UUID, body: Decision, request: Request) -> dict[str, Any]:
    edits = {str(proposal_id): body.edits} if body.edits else {}
    answer = {"approve": "all", "edits": edits, "note": body.note}
    return await _decide(request, [proposal_id], answer)


@protected.post("/proposals/{proposal_id}/reject")
async def reject(proposal_id: uuid.UUID, body: Decision, request: Request) -> dict[str, Any]:
    return await _decide(request, [proposal_id], {"approve": [], "note": body.note})


@protected.post("/proposals/approve-batch")
async def approve_batch(body: BatchDecision, request: Request) -> dict[str, Any]:
    return await _decide(request, body.ids, {"approve": "all", "note": body.note})


# --- chat --------------------------------------------------------------------------------


@protected.post("/chat")
async def chat(body: ChatBody, request: Request) -> StreamingResponse:
    """Server-sent events: run, tool, tool_refused, proposal, answer, error, done."""
    agent = ChatAgent(services(request).deps)
    history = [m.model_dump() for m in body.history]

    async def events() -> AsyncIterator[str]:
        async for event in agent.run(body.message, history):
            yield f"event: {event['type']}\ndata: {json.dumps(event, default=str)}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        # no-transform stops compressing proxies (Next's `next start`, CDNs) from buffering the
        # stream into one block; X-Accel-Buffering does the same for nginx.
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


# --- demo --------------------------------------------------------------------------------


@protected.post("/demo/reset")
async def demo_reset(request: Request) -> dict[str, str]:
    """Demo only: rebuild the shared sample workspace and clear runs and proposals."""
    svc = services(request)
    if not svc.settings.lifeos_demo_mode:
        raise ApiError(404, "http_error", "Not Found")
    if not svc.reset_limiter.allow("reset"):
        raise ApiError(429, "rate_limited", "the demo was just reset; try again in a minute", True)
    await reset_demo(svc.deps)
    return {"status": "reset"}


# --- settings and status -----------------------------------------------------------------


@protected.get("/settings")
async def get_settings_view(request: Request) -> dict[str, Any]:
    svc = services(request)
    llm = svc.deps.llm
    return {
        "demo": svc.settings.lifeos_demo_mode,
        "notion_profile": "demo" if svc.settings.lifeos_demo_mode else svc.settings.notion_profile,
        "writes_enabled": svc.deps.writes_enabled,
        "timezone": svc.settings.timezone,
        "today": svc.deps.today().isoformat(),  # in the owner's timezone, not the browser's
        "llm": {
            "configured": llm.provider is not None,
            "provider": llm.provider.name if llm.provider else None,
            "chains": llm.chains,
            "daily_token_budget": llm.daily_token_budget,
            "tokens_today": llm.tokens_spent_today(),
        },
        # Editable modes come later; every kind needs approval in v1.
        "modes": {str(kind): str(mode) for kind, mode in DEFAULT_MODES.items()},
    }


@protected.get("/notion/status")
async def notion_status(request: Request) -> dict[str, Any]:
    deps = services(request).deps
    me = await deps.notion.get_self()
    sources = []
    for name, spec in deps.specs.items():
        if not spec.configured:
            sources.append({"name": name, "configured": False})
            continue
        try:
            report = await check_data_source(deps.notion, spec)
        except NotionError as exc:
            sources.append({"name": name, "configured": True, "reachable": False,
                            "error": exc.code or type(exc).__name__})  # fmt: skip
            continue
        sources.append(
            {
                "name": name,
                "configured": True,
                "reachable": True,
                "title": report.title,
                "ok": report.ok,
                "drift": [
                    {
                        "field": i.field,
                        "property": i.property_name,
                        "problem": i.problem,
                        "detail": i.detail,
                        "blocking": i.blocking,
                    }
                    for i in report.issues
                ],  # fmt: skip
            }
        )
    return {
        "bot": me.get("name"),
        "workspace": (me.get("bot") or {}).get("workspace_name"),
        "data_sources": sources,
    }


# --- tasks -------------------------------------------------------------------------------


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
