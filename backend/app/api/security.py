"""Who may call the API (design §14).

Two ways in, both checked on every request except `/health` and login:
- the owner's browser session (HttpOnly cookie); requests that change something must also send
  `X-CSRF-Token` matching the session, which a cross-site page can't read or forge;
- `Authorization: Bearer <LIFEOS_API_TOKEN>` for scripts and the cron caller. If the token isn't
  configured, bearer requests are refused (fail closed).
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from fastapi import Header, Request

from app.api.errors import ApiError
from app.auth.sessions import SESSION_COOKIE, SessionService

if TYPE_CHECKING:
    from app.api.main import Services

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def services(request: Request) -> Services:
    return request.app.state.services


@dataclass(frozen=True)
class Principal:
    via: str  # "session" | "token"
    user_id: uuid.UUID | None = None
    email: str | None = None
    csrf_token: str | None = None


async def require_owner(
    request: Request,
    authorization: str | None = Header(None),
    x_csrf_token: str | None = Header(None),
) -> Principal:
    svc = services(request)
    if authorization:
        expected = svc.settings.lifeos_api_token
        if expected is None:
            raise ApiError(503, "auth_not_configured", "LIFEOS_API_TOKEN is not set")
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() == "bearer" and secrets.compare_digest(
            token.encode(), expected.get_secret_value().encode()
        ):
            return Principal("token")
        raise ApiError(401, "unauthorized", "missing or invalid bearer token")

    cookie = request.cookies.get(SESSION_COOKIE)
    if cookie:
        async with svc.deps.session_factory() as db:
            found = await SessionService(db, clock=svc.deps.clock).resolve(cookie)
        if found:
            session, user = found
            if request.method not in SAFE_METHODS and not (
                x_csrf_token and secrets.compare_digest(x_csrf_token, session.csrf_token)
            ):
                raise ApiError(403, "csrf_failed", "missing or invalid X-CSRF-Token header")
            return Principal("session", user.id, user.email, session.csrf_token)
    raise ApiError(401, "unauthorized", "sign in, or send a bearer token")
