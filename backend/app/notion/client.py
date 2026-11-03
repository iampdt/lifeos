"""Notion API client (API version 2025-09-03).

Responsibilities: authentication headers, rate limiting, retries with backoff, pagination and
typed errors. The two write methods (`update_page`, `create_page`) are called only by the
executor, after a proposal has been approved (architecture §3.4). There is no archive or delete
method.

The token is either fixed (an internal integration) or comes from a `TokenSource` (an account's
OAuth connection), which is asked once for a fresh token when Notion answers 401.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Protocol

import httpx

from app.notion.errors import (
    NotionError,
    NotionRateLimited,
    NotionUnauthorized,
    NotionUnavailable,
    error_from_response,
)
from app.notion.rate_limiter import AsyncTokenBucket

logger = logging.getLogger("lifeos.notion")

DEFAULT_BASE_URL = "https://api.notion.com/v1"
DEFAULT_VERSION = "2025-09-03"


class TokenSource(Protocol):
    async def token(self) -> str: ...

    async def refresh(self, rejected: str) -> str | None:
        """A new token after Notion rejected `rejected`, or None if there is none."""
        ...


class StaticToken:
    def __init__(self, token: str) -> None:
        self._token = token

    async def token(self) -> str:
        return self._token

    async def refresh(self, rejected: str) -> str | None:
        return None


class NotionClient:
    def __init__(
        self,
        token: str | TokenSource,
        *,
        base_url: str = DEFAULT_BASE_URL,
        notion_version: str = DEFAULT_VERSION,
        rate_limiter: AsyncTokenBucket | None = None,
        max_attempts: int = 5,
        deadline_seconds: float = 60.0,
        backoff_base: float = 0.5,
        backoff_cap: float = 8.0,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rng: Callable[[], float] = random.random,
    ) -> None:
        self._tokens = StaticToken(token) if isinstance(token, str) else token
        self._http = httpx.AsyncClient(
            base_url=base_url,
            headers={"Notion-Version": notion_version, "Content-Type": "application/json"},
            timeout=httpx.Timeout(20.0, connect=5.0),
            transport=transport,
        )
        self._limiter = rate_limiter or AsyncTokenBucket(rate=2.5)
        self._max_attempts = max_attempts
        self._deadline_seconds = deadline_seconds
        self._backoff_base = backoff_base
        self._backoff_cap = backoff_cap
        self._sleep = sleep
        self._rng = rng
        self.request_count = 0
        self.retry_count = 0

    async def __aenter__(self) -> NotionClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    def _backoff(self, attempt: int) -> float:
        # "Full jitter": a random delay up to an exponentially growing ceiling.
        return self._rng() * min(self._backoff_cap, self._backoff_base * 2 ** (attempt - 1))

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        idempotent: bool = True,
    ) -> dict[str, Any]:
        """Send a request with retries.

        A non-idempotent request (page creation) is retried only on 429, which Notion returns
        before doing any work. After a timeout or 5xx the page may already exist, so the error is
        raised and the executor's find-or-create step resolves it on the next attempt.
        """
        deadline = time.monotonic() + self._deadline_seconds
        token = await self._tokens.token()
        refreshed = False
        for attempt in range(1, self._max_attempts + 1):
            await self._limiter.acquire()
            self.request_count += 1
            started = time.monotonic()
            try:
                response = await self._http.request(
                    method, path, json=json, headers={"Authorization": f"Bearer {token}"}
                )
            except httpx.TimeoutException as exc:
                error: NotionError = NotionUnavailable(f"Timed out calling Notion: {exc!r}")
            except httpx.TransportError as exc:
                error = NotionUnavailable(f"Network error calling Notion: {exc!r}")
            else:
                elapsed_ms = int((time.monotonic() - started) * 1000)
                logger.debug(
                    "notion %s %s -> %s in %sms (attempt %s)",
                    method,
                    path,
                    response.status_code,
                    elapsed_ms,
                    attempt,
                )
                if response.status_code < 400:
                    return response.json()
                try:
                    body = response.json()
                except ValueError:
                    body = None
                error = error_from_response(response.status_code, body, response.headers)
                if isinstance(error, NotionUnauthorized) and not refreshed:
                    # Notion rejected the token before doing any work, so retrying is safe.
                    refreshed = True
                    fresh = await self._tokens.refresh(token)
                    if fresh is not None:
                        token = fresh
                        continue

            safe_to_retry = error.retryable and (idempotent or isinstance(error, NotionRateLimited))
            if not safe_to_retry or attempt == self._max_attempts:
                raise error
            delay = (
                error.retry_after
                if isinstance(error, NotionRateLimited) and error.retry_after is not None
                else self._backoff(attempt)
            )
            if time.monotonic() + delay > deadline:
                raise error
            self.retry_count += 1
            logger.warning(
                "notion %s %s failed (%s); retrying in %.2fs (attempt %s/%s)",
                method,
                path,
                error.code or type(error).__name__,
                delay,
                attempt,
                self._max_attempts,
            )
            await self._sleep(delay)
        raise AssertionError("unreachable")  # pragma: no cover

    # --- writes (executor only) -----------------------------------------------------------

    async def update_page(self, page_id: str, properties: dict[str, Any]) -> dict[str, Any]:
        # Setting property values is idempotent, so normal retries are safe.
        return await self._request("PATCH", f"pages/{page_id}", json={"properties": properties})

    async def create_page(self, data_source_id: str, properties: dict[str, Any]) -> dict[str, Any]:
        body = {
            "parent": {"type": "data_source_id", "data_source_id": data_source_id},
            "properties": properties,
        }
        return await self._request("POST", "pages", json=body, idempotent=False)

    # --- reads -----------------------------------------------------------------------------

    async def get_self(self) -> dict[str, Any]:
        return await self._request("GET", "users/me")

    async def search_data_sources(self, *, page_size: int = 100) -> AsyncIterator[dict[str, Any]]:
        """Every data source the integration can see (shared with it, or under shared pages)."""
        body: dict[str, Any] = {
            "filter": {"property": "object", "value": "data_source"},
            "page_size": page_size,
        }
        while True:
            data = await self._request("POST", "search", json=body)
            for item in data.get("results", []):
                yield item
            cursor = data.get("next_cursor")
            if not data.get("has_more") or not cursor:
                return
            body = {**body, "start_cursor": cursor}

    async def search_pages(self, query: str, *, page_size: int = 20) -> list[dict[str, Any]]:
        """Pages (not databases) the integration can see whose title matches, best first; one
        page of results is enough for a picker."""
        body = {"query": query, "filter": {"property": "object", "value": "page"},
                "page_size": page_size}  # fmt: skip
        data = await self._request("POST", "search", json=body)
        return list(data.get("results", []))

    async def list_block_children(self, block_id: str) -> AsyncIterator[dict[str, Any]]:
        """A page's (or block's) direct children, in order, following `next_cursor`."""
        cursor: str | None = None
        while True:
            path = f"blocks/{block_id}/children?page_size=100"
            if cursor:
                path += f"&start_cursor={cursor}"
            data = await self._request("GET", path)
            for item in data.get("results", []):
                yield item
            cursor = data.get("next_cursor")
            if not data.get("has_more") or not cursor:
                return

    async def get_database(self, database_id: str) -> dict[str, Any]:
        return await self._request("GET", f"databases/{database_id}")

    async def get_data_source(self, data_source_id: str) -> dict[str, Any]:
        return await self._request("GET", f"data_sources/{data_source_id}")

    async def get_page(self, page_id: str) -> dict[str, Any]:
        return await self._request("GET", f"pages/{page_id}")

    async def query_data_source(
        self,
        data_source_id: str,
        *,
        filter: dict[str, Any] | None = None,
        sorts: list[dict[str, Any]] | None = None,
        page_size: int = 100,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield every page matching the query, following `next_cursor` until exhausted."""
        body: dict[str, Any] = {"page_size": page_size}
        if filter:
            body["filter"] = filter
        if sorts:
            body["sorts"] = sorts
        while True:
            data = await self._request("POST", f"data_sources/{data_source_id}/query", json=body)
            for item in data.get("results", []):
                yield item
            cursor = data.get("next_cursor")
            if not data.get("has_more") or not cursor:
                return
            body = {**body, "start_cursor": cursor}

    async def get_relation_ids(self, page_id: str, property_id: str) -> list[str]:
        """Full list of related page IDs, for relations longer than the page object returns."""
        ids: list[str] = []
        cursor: str | None = None
        while True:
            path = f"pages/{page_id}/properties/{property_id}"
            if cursor:
                path += f"?start_cursor={cursor}"
            data = await self._request("GET", path)
            for item in data.get("results", []):
                relation = item.get("relation") or {}
                if relation.get("id"):
                    ids.append(relation["id"])
            cursor = data.get("next_cursor")
            if not data.get("has_more") or not cursor:
                return ids
