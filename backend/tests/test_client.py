import json

import httpx
import pytest

from app.notion.client import NotionClient
from app.notion.errors import (
    NotionNotShared,
    NotionRateLimited,
    NotionUnavailable,
    NotionValidation,
)
from app.notion.rate_limiter import AsyncTokenBucket


def error_body(status: int, code: str) -> dict:
    return {
        "object": "error",
        "status": status,
        "code": code,
        "message": code,
        "request_id": "req-1",
    }


def make_client(handler, **kwargs):
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    client = NotionClient(
        "secret-token",
        transport=httpx.MockTransport(handler),
        rate_limiter=AsyncTokenBucket(rate=1000, capacity=1000),
        sleep=fake_sleep,
        rng=lambda: 0.5,
        **kwargs,
    )
    return client, slept


async def test_sends_auth_and_version_headers():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/users/me"
        assert request.headers["Authorization"] == "Bearer secret-token"
        assert request.headers["Notion-Version"] == "2025-09-03"
        return httpx.Response(200, json={"object": "user", "name": "LifeOS"})

    client, _ = make_client(handler)
    async with client:
        assert (await client.get_self())["name"] == "LifeOS"


async def test_rate_limited_request_waits_for_retry_after_then_succeeds():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(
                429, json=error_body(429, "rate_limited"), headers={"Retry-After": "2"}
            )
        return httpx.Response(200, json={"object": "user"})

    client, slept = make_client(handler)
    async with client:
        await client.get_self()
    assert slept == [2.0]
    assert client.retry_count == 1 and client.request_count == 2


async def test_server_errors_retry_with_backoff_then_give_up():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json=error_body(503, "service_unavailable"))

    client, slept = make_client(handler, max_attempts=3)
    async with client:
        with pytest.raises(NotionUnavailable):
            await client.get_self()
    # Full jitter with rng=0.5: 0.5 * 0.5s, then 0.5 * 1s.
    assert slept == [0.25, 0.5]
    assert client.request_count == 3


async def test_network_errors_are_retried():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ConnectError("boom", request=request)
        return httpx.Response(200, json={"object": "user"})

    client, slept = make_client(handler)
    async with client:
        await client.get_self()
    assert len(slept) == 1


@pytest.mark.parametrize(
    ("status", "code", "error"),
    [(404, "object_not_found", NotionNotShared), (400, "validation_error", NotionValidation)],
)
async def test_client_errors_fail_fast_without_retry(status, code, error):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=error_body(status, code))

    client, slept = make_client(handler)
    async with client:
        with pytest.raises(error) as exc_info:
            await client.get_page("missing")
    assert slept == [] and client.request_count == 1
    assert exc_info.value.request_id == "req-1"


async def test_retry_after_beyond_deadline_raises_immediately():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429, json=error_body(429, "rate_limited"), headers={"Retry-After": "30"}
        )

    client, slept = make_client(handler, deadline_seconds=5)
    async with client:
        with pytest.raises(NotionRateLimited):
            await client.get_self()
    assert slept == []


async def test_query_follows_cursors_and_keeps_the_filter():
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/v1/data_sources/ds-1/query"
        body = json.loads(request.content)
        bodies.append(body)
        if "start_cursor" not in body:
            return httpx.Response(
                200,
                json={
                    "results": [{"object": "page", "id": "a"}],
                    "has_more": True,
                    "next_cursor": "c2",
                },
            )
        return httpx.Response(
            200, json={"results": [{"object": "page", "id": "b"}], "has_more": False}
        )

    client, _ = make_client(handler)
    query_filter = {"property": "Status", "select": {"equals": "Next"}}
    async with client:
        ids = [p["id"] async for p in client.query_data_source("ds-1", filter=query_filter)]
    assert ids == ["a", "b"]
    assert bodies[1]["start_cursor"] == "c2" and bodies[1]["filter"] == query_filter
    assert all(b["page_size"] == 100 for b in bodies)


async def test_relation_ids_are_paginated():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/pages/p1/properties/rel"
        if "start_cursor" not in request.url.params:
            results = [{"object": "property_item", "type": "relation", "relation": {"id": "x"}}]
            return httpx.Response(
                200, json={"results": results, "has_more": True, "next_cursor": "n"}
            )
        results = [{"object": "property_item", "type": "relation", "relation": {"id": "y"}}]
        return httpx.Response(200, json={"results": results, "has_more": False})

    client, _ = make_client(handler)
    async with client:
        assert await client.get_relation_ids("p1", "rel") == ["x", "y"]
