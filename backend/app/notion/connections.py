"""Accounts' Notion connections: OAuth tokens stored encrypted, refreshed when Notion rejects them
(multi-user §Notion connection).

Refreshing rotates the refresh token: the old one stops working. Two requests refreshing at
once would race, and the loser would lock the account out, so a refresh locks the connection's
row, and a request that finds the token already replaced uses the new one instead of refreshing
again. The lock is held across the call to Notion's token endpoint; that is its purpose.

A refresh that Notion refuses (the access was removed in Notion, or the grant expired) marks the
connection `needs_reconnect`. A refresh that fails for any other reason (a network error) leaves
it alone, so it can be tried again.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.crypto import TokenCipher
from app.db.models import NotionConnection
from app.execution.audit import audit
from app.notion.client import DEFAULT_BASE_URL, DEFAULT_VERSION, NotionClient
from app.notion.errors import NotionUnauthorized, NotionUnavailable
from app.notion.oauth import NotionOAuth, NotionOAuthError, TokenGrant
from app.notion.rate_limiter import AsyncTokenBucket

logger = logging.getLogger("lifeos.notion")

# Refresh a token this long before Notion says it expires (when it says so at all).
REFRESH_MARGIN = timedelta(minutes=1)


def utcnow() -> datetime:
    return datetime.now(UTC)


class ReconnectNeeded(NotionUnauthorized):
    """The account has no usable connection: it was never made, was disconnected, or Notion
    refused to refresh it. Only connecting again helps."""


class ConnectionTokens:
    """The `TokenSource` for one account's Notion client."""

    def __init__(self, connections: NotionConnections, user_id: uuid.UUID) -> None:
        self._connections = connections
        self._user_id = user_id

    async def token(self) -> str:
        return await self._connections.access_token(self._user_id)

    async def refresh(self, rejected: str) -> str | None:
        return await self._connections.refresh(self._user_id, rejected)


class NotionConnections:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        cipher: TokenCipher,
        oauth: NotionOAuth,
        *,
        base_url: str = DEFAULT_BASE_URL,
        notion_version: str = DEFAULT_VERSION,
        requests_per_second: float = 2.5,
        clock: Callable[[], datetime] = utcnow,
        transport: httpx.AsyncBaseTransport | None = None,  # tests: a fake Notion
    ) -> None:
        self.session_factory = session_factory
        self.cipher = cipher
        self.oauth = oauth
        self.base_url = base_url
        self.notion_version = notion_version
        self.requests_per_second = requests_per_second
        self.clock = clock
        self.transport = transport
        # One rate limiter per account, kept across requests: Notion's limit is per connection.
        self._limiters: dict[uuid.UUID, AsyncTokenBucket] = {}

    async def get(self, user_id: uuid.UUID) -> NotionConnection | None:
        async with self.session_factory() as db:
            return await db.get(NotionConnection, user_id)

    async def save(self, user_id: uuid.UUID, grant: TokenGrant) -> None:
        """Store a new connection, replacing the account's previous one."""
        values = {
            "user_id": user_id,
            "status": "active",
            "refresh_token": None,
            "duplicated_template_id": None,
            "refreshed_at": None,
            **self._grant_values(grant),
            "connected_at": self.clock(),
        }
        async with self.session_factory() as db, db.begin():
            await db.execute(
                insert(NotionConnection)
                .values(**values)
                .on_conflict_do_update(index_elements=["user_id"], set_=values)
            )
            audit(db, actor="user", event="notion.connected", user_id=user_id,
                  details={"workspace_id": grant.workspace_id,
                           "template": grant.duplicated_template_id is not None})  # fmt: skip

    async def disconnect(self, user_id: uuid.UUID) -> tuple[bool, bool]:
        """Revoke the token at Notion (best effort) and delete it here.
        Returns (was connected, revoked at Notion)."""
        connection = await self.get(user_id)
        if connection is None:
            return False, False
        revoked = True
        try:
            await self.oauth.revoke(self.cipher.decrypt(connection.access_token))
        except NotionOAuthError as exc:
            revoked = exc.revoked  # an already revoked token counts as revoked
            logger.warning("revoking a Notion token failed: %s", exc)
        async with self.session_factory() as db, db.begin():
            await db.execute(delete(NotionConnection).where(NotionConnection.user_id == user_id))
            audit(db, actor="user", event="notion.disconnected", user_id=user_id,
                  details={"revoked": revoked})  # fmt: skip
        self._limiters.pop(user_id, None)
        return True, revoked

    def client(self, user_id: uuid.UUID) -> NotionClient:
        """A Notion client acting for the account. Close it after use."""
        limiter = self._limiters.setdefault(
            user_id, AsyncTokenBucket(rate=self.requests_per_second)
        )
        return NotionClient(
            ConnectionTokens(self, user_id),
            base_url=self.base_url,
            notion_version=self.notion_version,
            rate_limiter=limiter,
            transport=self.transport,
        )

    # --- tokens --------------------------------------------------------------------------

    async def access_token(self, user_id: uuid.UUID) -> str:
        connection = await self.get(user_id)
        if connection is None or connection.status != "active":
            raise ReconnectNeeded("Notion isn't connected for this account", code="reconnect")
        current = self.cipher.decrypt(connection.access_token)
        if connection.expires_at and connection.expires_at - REFRESH_MARGIN <= self.clock():
            fresh = await self.refresh(user_id, current)
            if fresh is None:
                raise ReconnectNeeded("Notion access expired; connect again", code="reconnect")
            return fresh
        return current

    async def refresh(self, user_id: uuid.UUID, rejected: str) -> str | None:
        async with self.session_factory() as db, db.begin():
            connection = await db.get(NotionConnection, user_id, with_for_update=True)
            if connection is None or connection.status != "active":
                return None
            current = self.cipher.decrypt(connection.access_token)
            if current != rejected:
                return current  # another request refreshed it while this one waited
            if connection.refresh_token is None:
                self._needs_reconnect(db, connection, "no refresh token")
                return None
            try:
                grant = await self.oauth.refresh(self.cipher.decrypt(connection.refresh_token))
            except NotionOAuthError as exc:
                if not exc.revoked:
                    raise NotionUnavailable(f"refreshing the Notion token failed: {exc}") from exc
                self._needs_reconnect(db, connection, str(exc))
                return None
            for name, value in self._grant_values(grant).items():
                setattr(connection, name, value)
            connection.refreshed_at = self.clock()
            return grant.access_token

    def _needs_reconnect(self, db: AsyncSession, connection: NotionConnection, why: str) -> None:
        connection.status = "needs_reconnect"
        audit(db, actor="system", event="notion.needs_reconnect", user_id=connection.user_id,
              details={"reason": why[:200]})  # fmt: skip

    def _grant_values(self, grant: TokenGrant) -> dict[str, Any]:
        values: dict[str, Any] = {
            "workspace_id": grant.workspace_id,
            "workspace_name": (grant.workspace_name or "")[:200] or None,
            "workspace_icon": (grant.workspace_icon or "")[:500] or None,
            "bot_id": grant.bot_id,
            "access_token": self.cipher.encrypt(grant.access_token),
            "expires_at": (
                self.clock() + timedelta(seconds=grant.expires_in) if grant.expires_in else None
            ),
        }
        if grant.refresh_token:  # a refresh that returns none keeps the previous one
            values["refresh_token"] = self.cipher.encrypt(grant.refresh_token)
        if grant.duplicated_template_id:
            values["duplicated_template_id"] = grant.duplicated_template_id
        return values
