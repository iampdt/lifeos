"""An in-memory Notion: the demo deployment's workspace, and the fake used by tests.

It accepts the same write JSON as the real API and returns pages in the real response shape,
so encoders, codecs, repositories, queries and read-after-write verification all run unchanged
on top of it.
"""

from __future__ import annotations

import copy
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from app.notion.codecs import DateValue, decode_property
from app.notion.encoders import encode_properties
from app.notion.errors import NotionNotShared
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
    """The subset of Notion's filter language LifeOS uses."""
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
    raise NotImplementedError(f"in-memory filter {kind}.{op}")


def schema_for(spec: DataSourceSpec) -> dict[str, Any]:
    """A data-source object whose properties exactly match the mapping."""
    properties: dict[str, Any] = {}
    for prop in spec.properties.values():
        body: dict[str, Any] = {}
        if prop.type in ("select", "status", "multi_select"):
            body = {
                "options": [{"id": v, "name": v, "color": "default"} for v in prop.values.values()]
            }
        properties[prop.name] = {
            "id": prop.name[:4],
            "name": prop.name,
            "type": prop.type,
            prop.type: body,
        }
    title = spec.logical_name.title()
    return {
        "object": "data_source",
        "id": spec.data_source_id,
        "title": [{"type": "text", "text": {"content": title}, "plain_text": title}],
        "properties": properties,
    }


class InMemoryNotion:
    def __init__(
        self,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        page_url: Callable[[str], str] = lambda page_id: "",
        identity: dict[str, Any] | None = None,
    ) -> None:
        self.pages: dict[str, dict[str, Any]] = {}
        self.blocks: dict[str, list[dict[str, Any]]] = {}  # page or block id → children
        self.schemas: dict[str, dict[str, Any]] = {}  # data-source id → schema object
        self.update_calls = 0
        self.create_calls = 0
        self._clock = clock
        self._last: datetime | None = None
        self._page_url = page_url
        self._identity = identity or {"object": "user", "name": "LifeOS",
                                      "bot": {"workspace_name": "In-memory workspace"}}  # fmt: skip

    def _now(self) -> str:
        # Strictly increasing, so "edited after" comparisons behave like the real API.
        now = self._clock()
        if self._last is not None and now <= self._last:
            now = self._last + timedelta(seconds=1)
        self._last = now
        return now.astimezone(UTC).isoformat().replace("+00:00", "Z")

    def _new_page(self, data_source_id: str, properties: dict[str, Any]) -> dict[str, Any]:
        page_id = str(uuid.uuid4())
        page = {
            "object": "page",
            "id": page_id,
            "url": self._page_url(page_id),
            "in_trash": False,
            "created_time": self._now(),
            "last_edited_time": self._now(),
            "parent": {"type": "data_source_id", "data_source_id": data_source_id},
            "properties": properties,
        }
        self.pages[page_id] = page
        return page

    def clear(self) -> None:
        self.pages.clear()
        self.blocks.clear()

    # --- helpers for seeding and tests --------------------------------------------------------

    def seed(self, spec: DataSourceSpec, values: dict[str, Any]) -> str:
        properties = {
            name: _to_response(body)
            for name, body in encode_properties(spec.properties, values).items()
        }
        return self._new_page(spec.data_source_id, properties)["id"]

    def add_document(self, title: str, blocks: list[tuple[str, str]]) -> str:
        """A standalone page whose content is the given (block type, text) pairs, all at the
        top level."""
        page = self._new_page("", {"title": {"id": "title", "type": "title",
                                             "title": [_text(title)]}})  # fmt: skip
        page["parent"] = {"type": "workspace", "workspace": True}
        self.blocks[page["id"]] = [_block(kind, text) for kind, text in blocks]
        return page["id"]

    def set_blocks(self, page_id: str, blocks: list[tuple[str, str]]) -> None:
        """Simulate the owner rewriting a page's content."""
        self.blocks[page_id] = [_block(kind, text) for kind, text in blocks]
        self.pages[page_id]["last_edited_time"] = self._now()

    def edit(self, page_id: str, spec: DataSourceSpec, values: dict[str, Any]) -> None:
        """Simulate the owner editing a page in Notion."""
        for name, body in encode_properties(spec.properties, values).items():
            self.pages[page_id]["properties"][name] = _to_response(body)
        self.pages[page_id]["last_edited_time"] = self._now()

    # --- the client interface the app uses ----------------------------------------------------

    async def get_page(self, page_id: str) -> dict[str, Any]:
        if page_id not in self.pages:
            raise NotionNotShared("not found", status=404, code="object_not_found")
        return copy.deepcopy(self.pages[page_id])

    async def update_page(self, page_id: str, properties: dict[str, Any]) -> dict[str, Any]:
        self.update_calls += 1
        page = self.pages[page_id]
        for name, body in properties.items():
            page["properties"][name] = _to_response(body)
        page["last_edited_time"] = self._now()
        return copy.deepcopy(page)

    async def create_page(self, data_source_id: str, properties: dict[str, Any]) -> dict[str, Any]:
        self.create_calls += 1
        page = self._new_page(
            data_source_id, {name: _to_response(body) for name, body in properties.items()}
        )
        return copy.deepcopy(page)

    async def query_data_source(
        self, data_source_id: str, *, filter=None, sorts=None, page_size=100
    ):
        for page in list(self.pages.values()):
            if page["parent"].get("data_source_id") == data_source_id and _matches(page, filter):
                yield copy.deepcopy(page)

    async def get_relation_ids(self, page_id: str, property_id: str) -> list[str]:
        raise AssertionError("in-memory pages never truncate relations")

    async def search_pages(self, query: str, *, page_size: int = 20) -> list[dict[str, Any]]:
        words = query.lower().split()
        found = [
            copy.deepcopy(page) for page in self.pages.values()
            if all(w in _title_of(page).lower() for w in words)
        ]  # fmt: skip
        return found[:page_size]

    async def list_block_children(self, block_id: str):
        if block_id not in self.blocks and block_id not in self.pages:
            raise NotionNotShared("not found", status=404, code="object_not_found")
        for block in self.blocks.get(block_id, []):
            yield copy.deepcopy(block)

    async def get_self(self) -> dict[str, Any]:
        return copy.deepcopy(self._identity)

    async def get_data_source(self, data_source_id: str) -> dict[str, Any]:
        if data_source_id not in self.schemas:
            raise NotionNotShared("not shared", status=404, code="object_not_found")
        return copy.deepcopy(self.schemas[data_source_id])


def _text(content: str) -> dict[str, Any]:
    return {"type": "text", "text": {"content": content}, "plain_text": content}


def _block(kind: str, content: str) -> dict[str, Any]:
    body: dict[str, Any] = {"rich_text": [_text(content)]}
    if kind == "to_do":
        body["checked"] = False
    return {"object": "block", "id": str(uuid.uuid4()), "type": kind, "has_children": False,
            kind: body}  # fmt: skip


def _title_of(page: dict[str, Any]) -> str:
    for prop in page.get("properties", {}).values():
        if prop.get("type") == "title":
            return "".join(part.get("plain_text", "") for part in prop.get("title", []))
    return ""
