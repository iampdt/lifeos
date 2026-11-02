"""Notion's HTTP API in front of an in-memory workspace, for tests that go through a real
`NotionClient` (an account's OAuth connection): token exchange, data sources, queries, pages,
search."""

from __future__ import annotations

import json

import httpx

from app.notion.errors import NotionError
from tests.fake_notion import FakeNotion


class NotionOverHTTP:
    def __init__(self, notion: FakeNotion, *, workspace_name: str = "Acme") -> None:
        self.notion = notion
        self.workspace_name = workspace_name
        self.tokens: set[str] = set()
        self.issued = 0

    def _grant(self) -> dict:
        self.issued += 1
        token = f"access-{self.issued}"
        self.tokens.add(token)
        return {"access_token": token, "refresh_token": f"refresh-{self.issued}",
                "token_type": "bearer", "bot_id": "bot", "workspace_id": "ws",
                "workspace_name": self.workspace_name, "workspace_icon": None,
                "duplicated_template_id": None, "owner": {"type": "user"}}  # fmt: skip

    async def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/v1/")
        body = json.loads(request.content) if request.content else {}
        if path == "oauth/token":
            return httpx.Response(200, json=self._grant())
        if path == "oauth/revoke":
            self.tokens.discard(body.get("token"))
            return httpx.Response(200, json={})
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        if token not in self.tokens:
            return _error(401, "unauthorized")
        try:
            return httpx.Response(200, json=await self._data(request.method, path, body))
        except NotionError as exc:
            return _error(exc.status or 400, exc.code or "validation_error")

    async def _data(self, method: str, path: str, body: dict) -> dict:
        parts = path.split("/")
        match method, parts:
            case "GET", ["users", "me"]:
                return await self.notion.get_self()
            case "GET", ["data_sources", source]:
                return await self.notion.get_data_source(source)
            case "POST", ["data_sources", source, "query"]:
                results = [p async for p in self.notion.query_data_source(
                    source, filter=body.get("filter"))]  # fmt: skip
                return {"object": "list", "results": results, "has_more": False}
            case "GET", ["pages", page]:
                return await self.notion.get_page(page)
            case "PATCH", ["pages", page]:
                return await self.notion.update_page(page, body["properties"])
            case "POST", ["pages"]:
                return await self.notion.create_page(
                    body["parent"]["data_source_id"], body["properties"]
                )
            case "POST", ["search"]:
                return {"results": list(self.notion.schemas.values()), "has_more": False}
        raise AssertionError(f"unexpected Notion call {method} {path}")


def _error(status: int, code: str) -> httpx.Response:
    return httpx.Response(status, json={"object": "error", "status": status, "code": code,
                                        "message": code})  # fmt: skip
