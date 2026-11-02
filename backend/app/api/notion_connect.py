"""Connect an account's own Notion workspace (multi-user §Notion connection).

`GET /notion/connect` starts Notion's OAuth flow from a signed-in browser; Notion sends the user
back to `GET /notion/callback`, which lands on the Settings page with `?notion=connected` or
`?notion_error=<code>`. The attempt is tied to the account that started it, so a callback from
someone else's attempt (a forged link) can't attach their workspace to this account.
"""

from __future__ import annotations

import secrets
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import delete

from app.api.errors import ApiError
from app.api.security import CurrentUser, services
from app.auth.sessions import SESSION_COOKIE, SessionService
from app.db.models import OAuthState
from app.notion.connections import NotionConnections
from app.notion.errors import NotionUnauthorized
from app.notion.oauth import NotionOAuthError

router = APIRouter()

ATTEMPT_TTL = timedelta(minutes=10)
MAX_DATA_SOURCES = 200


def _connections(request: Request) -> NotionConnections:
    connections = services(request).connections
    if connections is None:
        raise ApiError(404, "http_error", "Not Found")
    return connections


@router.get("/notion/connect")
async def connect(request: Request, principal: CurrentUser) -> RedirectResponse:
    svc = services(request)
    connections = _connections(request)
    if principal.via != "session":
        raise ApiError(403, "session_required", "connect Notion from a signed-in browser")
    if not svc.oauth_limiter.allow(f"notion:{principal.user_id}"):
        raise ApiError(429, "rate_limited", "too many attempts; wait a minute", True)
    state = secrets.token_urlsafe(32)
    now = svc.clock()
    async with svc.session_factory() as db, db.begin():
        await db.execute(delete(OAuthState).where(OAuthState.expires_at < now))
        db.add(OAuthState(state=state, provider="notion", user_id=principal.user_id,
                          expires_at=now + ATTEMPT_TTL))  # fmt: skip
    return RedirectResponse(connections.oauth.authorization_url(state=state), status_code=302)


@router.get("/notion/callback")
async def callback(
    request: Request, code: str = "", state: str = "", error: str = ""
) -> RedirectResponse:
    svc = services(request)
    connections = _connections(request)
    try:
        attempt = None
        if state:
            async with svc.session_factory() as db, db.begin():
                attempt = await db.get(OAuthState, state, with_for_update=True)
                if attempt is not None:
                    await db.delete(attempt)  # single use, whatever happens next
        if error:
            raise NotionOAuthError("cancelled", error)
        if attempt is None or attempt.provider != "notion" or attempt.expires_at <= svc.clock():
            raise NotionOAuthError("expired", "the connection attempt expired; try again")
        cookie = request.cookies.get(SESSION_COOKIE)
        found = None
        if cookie:
            async with svc.session_factory() as db:
                found = await SessionService(db, clock=svc.clock).resolve(cookie)
        if found is None or found[1].id != attempt.user_id:
            raise NotionOAuthError("wrong_account", "finish connecting in the account that began")
        if not code:
            raise NotionOAuthError("cancelled", "no authorisation code")
        grant = await connections.oauth.exchange(code)
        await connections.save(attempt.user_id, grant)
    except NotionOAuthError as exc:
        return RedirectResponse(f"/settings?notion_error={exc.code}", status_code=302)
    return RedirectResponse("/settings?notion=connected", status_code=302)


@router.get("/notion/connection")
async def connection(request: Request, principal: CurrentUser) -> dict[str, Any]:
    connections = services(request).connections
    row = await connections.get(principal.user_id) if connections else None
    return {
        "available": connections is not None,
        "connection": None
        if row is None
        else {
            "workspace_id": row.workspace_id,
            "workspace_name": row.workspace_name,
            "workspace_icon": row.workspace_icon,
            "status": row.status,
            "connected_at": row.connected_at.isoformat(),
            "from_template": row.duplicated_template_id is not None,
        },
    }


@router.post("/notion/disconnect")
async def disconnect(request: Request, principal: CurrentUser) -> dict[str, Any]:
    was_connected, revoked = await _connections(request).disconnect(principal.user_id)
    if not was_connected:
        raise ApiError(409, "not_connected", "Notion isn't connected")
    return {"status": "disconnected", "revoked": revoked}


@router.get("/notion/data-sources")
async def data_sources(request: Request, principal: CurrentUser) -> dict[str, Any]:
    """The databases (data sources) the account's connection can see."""
    client = _connections(request).client(principal.user_id)
    found: list[dict[str, Any]] = []
    try:
        async for item in client.search_data_sources():
            parent = item.get("parent") or {}
            found.append(
                {
                    "id": item["id"],
                    "title": "".join(t.get("plain_text", "") for t in item.get("title") or []),
                    "database_id": parent.get("database_id"),
                    "url": item.get("url"),
                }
            )
            if len(found) == MAX_DATA_SOURCES:
                break
    except NotionUnauthorized as exc:
        raise ApiError(409, "notion_reconnect", "connect Notion again to continue") from exc
    finally:
        await client.aclose()
    return {"data_sources": found, "truncated": len(found) == MAX_DATA_SOURCES}
