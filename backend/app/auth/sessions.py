"""Server-side sessions for the owner (decision D7, architecture §9).

The browser holds a random token in an HttpOnly cookie; the database stores only its SHA-256.
Each session also has a CSRF token, returned by login and `/auth/me`, that the frontend sends
as `X-CSRF-Token` on every request that changes something.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.passwords import hash_password, needs_rehash, verify_password
from app.db.models import Session, User
from app.execution.audit import audit

SESSION_COOKIE = "lifeos_session"


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def normalize_email(email: str) -> str:
    return email.strip().lower()


class SessionService:
    def __init__(
        self,
        db: AsyncSession,
        *,
        ttl: timedelta = timedelta(days=14),
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.db = db
        self.ttl = ttl
        self.clock = clock

    async def login(self, email: str, password: str) -> tuple[str, Session, User] | None:
        user = await self.db.scalar(select(User).where(User.email == normalize_email(email)))
        if not verify_password(user.password_hash if user else None, password):
            audit(self.db, actor="system", event="auth.login_failed", user_id=None)
            return None
        assert user is not None
        if needs_rehash(user.password_hash):
            user.password_hash = hash_password(password)
        token, session = await self.start(user)
        return token, session, user

    async def start(self, user: User) -> tuple[str, Session]:
        """A new session for an already-authenticated user (password, Google, or the demo)."""
        token = secrets.token_urlsafe(32)
        session = Session(
            user_id=user.id,
            token_hash=token_hash(token),
            csrf_token=secrets.token_urlsafe(32),
            expires_at=self.clock() + self.ttl,
        )
        self.db.add(session)
        await self.db.flush()
        audit(self.db, actor="user", event="auth.login", user_id=user.id, entity_type="user",
              entity_id=str(user.id))  # fmt: skip
        return token, session

    async def resolve(self, token: str) -> tuple[Session, User] | None:
        row = await self.db.execute(
            select(Session, User)
            .join(User, User.id == Session.user_id)
            .where(
                Session.token_hash == token_hash(token),
                Session.revoked_at.is_(None),
                Session.expires_at > self.clock(),
            )
        )
        found = row.first()
        return (found[0], found[1]) if found else None

    async def logout(self, token: str) -> None:
        session = await self.db.scalar(
            select(Session).where(Session.token_hash == token_hash(token))
        )
        if session and session.revoked_at is None:
            session.revoked_at = self.clock()
            audit(self.db, actor="user", event="auth.logout", user_id=session.user_id,
                  entity_type="user", entity_id=str(session.user_id))  # fmt: skip

    async def create_owner(self, email: str, password: str) -> User:
        """The deployment owner's account (see `User.is_owner`); there can be only one."""
        user = User(
            email=normalize_email(email), password_hash=hash_password(password), is_owner=True
        )
        self.db.add(user)
        await self.db.flush()
        audit(self.db, actor="system", event="auth.owner_created", user_id=user.id,
              entity_type="user", entity_id=str(user.id))  # fmt: skip
        return user


class RateLimiter:
    """Sliding window, in memory. LifeOS runs as one process, so this is enough for one owner."""

    def __init__(
        self, limit: int, window_seconds: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.limit = limit
        self.window = window_seconds
        self.clock = clock
        self._hits: dict[str, deque[float]] = {}

    def allow(self, key: str) -> bool:
        now = self.clock()
        hits = self._hits.setdefault(key, deque())
        while hits and hits[0] <= now - self.window:
            hits.popleft()
        if len(hits) >= self.limit:
            return False
        hits.append(now)
        return True
