"""Who may call the API (architecture §9).

Two ways in, both checked on every request except `/health` and sign-in:
- an account's browser session (HttpOnly cookie); requests that change something must also send
  `X-CSRF-Token` matching the session, which a cross-site page can't read or forge;
- `Authorization: Bearer <LIFEOS_API_TOKEN>` for scripts, acting as the owner account. If the
  token isn't configured, bearer requests are refused (fail closed).

Every request then works on the caller's own data only: `user_context` gives the account's
workspace and runner, and those scope every query to the account.
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated

from fastapi import Depends, Header, Request
from sqlalchemy import select

from app.api.errors import ApiError
from app.auth.sessions import SESSION_COOKIE, SessionService
from app.db.models import User
from app.workflows.contexts import UserContext

if TYPE_CHECKING:
    from app.api.main import Services

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def services(request: Request) -> Services:
    return request.app.state.services


@dataclass(frozen=True)
class Principal:
    via: str  # "session" | "token"
    user_id: uuid.UUID
    email: str
    is_owner: bool
    name: str | None = None
    csrf_token: str | None = None


async def require_user(
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
        # Surrounding whitespace is ignored: pasted secrets often carry a stray space or newline.
        if not (
            scheme.lower() == "bearer"
            and secrets.compare_digest(
                token.strip().encode(), expected.get_secret_value().strip().encode()
            )
        ):
            raise ApiError(401, "unauthorized", "missing or invalid bearer token")
        async with svc.session_factory() as db:
            owner = await db.scalar(select(User).where(User.is_owner))
        if owner is None:
            raise ApiError(503, "no_owner", "there is no owner account for the bearer token")
        return Principal("token", owner.id, owner.email, True, owner.name)

    cookie = request.cookies.get(SESSION_COOKIE)
    if cookie:
        async with svc.session_factory() as db:
            found = await SessionService(db, clock=svc.clock).resolve(cookie)
        if found:
            session, user = found
            if request.method not in SAFE_METHODS and not (
                x_csrf_token and secrets.compare_digest(x_csrf_token, session.csrf_token)
            ):
                raise ApiError(403, "csrf_failed", "missing or invalid X-CSRF-Token header")
            return Principal(
                "session", user.id, user.email, user.is_owner, user.name, session.csrf_token
            )
    raise ApiError(401, "unauthorized", "sign in, or send a bearer token")


async def user_context(
    request: Request, principal: Annotated[Principal, Depends(require_user)]
) -> UserContext:
    """The caller's workspace and runner. Accounts without a workspace get 409."""
    contexts = services(request).contexts
    context = await contexts.for_user(principal.user_id, is_owner=principal.is_owner)
    if context is None:
        raise ApiError(409, "no_workspace", "this account has no Notion workspace connected yet")
    return context


CurrentUser = Annotated[Principal, Depends(require_user)]
Context = Annotated[UserContext, Depends(user_context)]
