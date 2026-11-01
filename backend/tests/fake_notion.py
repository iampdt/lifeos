"""In-memory Notion stand-in for executor tests.

It accepts the same write JSON as the real API and returns pages in the real response shape,
so encoders, codecs and verification are exercised end to end without the network.
"""

from __future__ import annotations

import copy
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from app.notion.codecs import DateValue, decode_property
from app.notion.encoders import encode_properties
from app.notion.errors import NotionNotShared, NotionUnavailable
from app.notion.mapping import DataSourceSpec


def _to_response(body: dict[str, Any]) -> dict[str, Any]:
    (kind, value), = body.items()  # fmt: skip
    value = copy.deepcopy(value)
    if kind in ("title", "rich_text"):
        for part in value:
            part["plain_text"] = part["text"]["content"]
    prop: dict[str, Any] = {"id": kind[:4], "type": kind, kind: value}
    if kind == "relation":
        prop["has_more"] = False
    return prop


def _matches(page: dict[str, Any], flt: dict[str, Any] | None) -> bool:
    if not flt:
        return True
    if "and" in flt:
        return all(_matches(page, f) for f in flt["and"])
    if "or" in flt:
        return any(_matches(page, f) for f in flt["or"])
    raw = page["properties"].get(flt["property"])
    value = decode_property(raw) if raw else None
    kind, condition = next((k, v) for k, v in flt.items() if k != "property")
    op, target = next(iter(condition.items()))
    if kind == "date":
        current = value.start.isoformat() if isinstance(value, DateValue) else None
        if current is None:
            return op == "is_empty"
        return {
            "equals": current == target,
            "before": current < target,
            "on_or_before": current <= target,
            "after": current > target,
            "on_or_after": current >= target,
        }[op]
    if op == "equals":
        return value == target
    if op == "does_not_equal":
        return value != target
    if op == "is_not_empty":
        return bool(value)
    if op == "is_empty":
        return not value
    raise NotImplementedError(f"fake filter {kind}.{op}")


class FakeNotion:
    def __init__(self) -> None:
        self.pages: dict[str, dict[str, Any]] = {}
        self.update_calls = 0
        self.create_calls = 0
        self.fail_after_create = False  # simulate a timeout after Notion created the page
        self.ignore_on_write: set[str] = set()  # property names silently dropped → mismatch
        self._clock = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
        self.schemas: dict[str, dict[str, Any]] = {}  # data-source id → schema object

    def _tick(self) -> str:
        self._clock += timedelta(seconds=1)
        return self._clock.isoformat().replace("+00:00", "Z")

    def seed(self, spec: DataSourceSpec, values: dict[str, Any]) -> str:
        page_id = str(uuid.uuid4())
        properties = {
            name: _to_response(body)
            for name, body in encode_properties(spec.properties, values).items()
        }
        self.pages[page_id] = {
            "object": "page",
            "id": page_id,
            "url": f"https://www.notion.so/{page_id.replace('-', '')}",
            "in_trash": False,
            "created_time": self._tick(),
            "last_edited_time": self._tick(),
            "parent": {"type": "data_source_id", "data_source_id": spec.data_source_id},
            "properties": properties,
        }
        return page_id

    def edit(self, page_id: str, spec: DataSourceSpec, values: dict[str, Any]) -> None:
        """Simulate the owner editing a page in Notion."""
        for name, body in encode_properties(spec.properties, values).items():
            self.pages[page_id]["properties"][name] = _to_response(body)
        self.pages[page_id]["last_edited_time"] = self._tick()

    async def get_page(self, page_id: str) -> dict[str, Any]:
        if page_id not in self.pages:
            raise NotionNotShared("not found", status=404, code="object_not_found")
        return copy.deepcopy(self.pages[page_id])

    async def update_page(self, page_id: str, properties: dict[str, Any]) -> dict[str, Any]:
        self.update_calls += 1
        page = self.pages[page_id]
        for name, body in properties.items():
            if name not in self.ignore_on_write:
                page["properties"][name] = _to_response(body)
        page["last_edited_time"] = self._tick()
        return copy.deepcopy(page)

    async def create_page(self, data_source_id: str, properties: dict[str, Any]) -> dict[str, Any]:
        self.create_calls += 1
        page_id = str(uuid.uuid4())
        self.pages[page_id] = {
            "object": "page",
            "id": page_id,
            "url": f"https://www.notion.so/{page_id.replace('-', '')}",
            "in_trash": False,
            "created_time": self._tick(),
            "last_edited_time": self._tick(),
            "parent": {"type": "data_source_id", "data_source_id": data_source_id},
            "properties": {name: _to_response(body) for name, body in properties.items()},
        }
        if self.fail_after_create:
            self.fail_after_create = False
            raise NotionUnavailable("timed out (but the page was created)")
        return copy.deepcopy(self.pages[page_id])

    async def query_data_source(
        self, data_source_id: str, *, filter=None, sorts=None, page_size=100
    ):
        for page in list(self.pages.values()):
            if page["parent"]["data_source_id"] == data_source_id and _matches(page, filter):
                yield copy.deepcopy(page)

    async def get_relation_ids(self, page_id: str, property_id: str) -> list[str]:
        raise AssertionError("fake pages never truncate relations")

    async def get_self(self) -> dict[str, Any]:
        return {"object": "user", "name": "LifeOs", "bot": {"workspace_name": "Test workspace"}}

    async def get_data_source(self, data_source_id: str) -> dict[str, Any]:
        if data_source_id not in self.schemas:
            raise NotionNotShared("not shared", status=404, code="object_not_found")
        return copy.deepcopy(self.schemas[data_source_id])
