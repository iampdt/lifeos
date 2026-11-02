"""The in-memory Notion with test-only failure hooks and a fixed clock."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.demo.notion import InMemoryNotion
from app.notion.errors import NotionUnavailable


class FakeNotion(InMemoryNotion):
    def __init__(self) -> None:
        start = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
        ticks = iter(start + timedelta(seconds=i) for i in range(1, 10**9))
        super().__init__(
            clock=lambda: next(ticks),
            page_url=lambda page_id: f"https://www.notion.so/{page_id.replace('-', '')}",
            identity={"object": "user", "name": "LifeOs",
                      "bot": {"workspace_name": "Test workspace"}},
        )  # fmt: skip
        self.fail_after_create = False  # simulate a timeout after Notion created the page
        self.ignore_on_write: set[str] = set()  # property names silently dropped → mismatch

    async def update_page(self, page_id: str, properties: dict[str, Any]) -> dict[str, Any]:
        kept = {name: body for name, body in properties.items() if name not in self.ignore_on_write}
        page = await super().update_page(page_id, kept)
        return page

    async def create_page(self, data_source_id: str, properties: dict[str, Any]) -> dict[str, Any]:
        page = await super().create_page(data_source_id, properties)
        if self.fail_after_create:
            self.fail_after_create = False
            raise NotionUnavailable("timed out (but the page was created)")
        return page
