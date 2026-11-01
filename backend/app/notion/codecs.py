"""Decode Notion property values (API 2025-09-03) into plain Python values.

Notion wraps every value in `{"type": <kind>, <kind>: <payload>}`. Decoding is pure and
schema-agnostic; repositories decide which property means what (see `mapping.py`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any


@dataclass(frozen=True)
class DateValue:
    start: date | datetime
    end: date | datetime | None = None
    time_zone: str | None = None

    @property
    def is_datetime(self) -> bool:
        return isinstance(self.start, datetime)


@dataclass(frozen=True)
class RelationValue:
    ids: tuple[str, ...]
    has_more: bool = False


def parse_notion_datetime(value: str) -> date | datetime:
    """Notion dates are either `YYYY-MM-DD` or a full ISO-8601 timestamp."""
    if len(value) == 10:
        return date.fromisoformat(value)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def plain_text(rich_text: list[dict[str, Any]] | None) -> str:
    return "".join(part.get("plain_text", "") for part in rich_text or [])


def _decode_date(raw: dict[str, Any] | None) -> DateValue | None:
    if not raw or not raw.get("start"):
        return None
    end = raw.get("end")
    return DateValue(
        start=parse_notion_datetime(raw["start"]),
        end=parse_notion_datetime(end) if end else None,
        time_zone=raw.get("time_zone"),
    )


def _decode_formula(value: dict[str, Any]) -> Any:
    kind = value.get("type")
    if kind == "date":
        return _decode_date(value.get("date"))
    return value.get(kind) if kind else None


def _decode_rollup(value: dict[str, Any]) -> Any:
    kind = value.get("type")
    if kind == "date":
        return _decode_date(value.get("date"))
    if kind == "array":
        return [decode_property(item) for item in value.get("array") or []]
    return value.get(kind) if kind else None


def decode_property(prop: dict[str, Any]) -> Any:
    kind = prop.get("type")
    value = prop.get(kind) if kind else None
    match kind:
        case "title" | "rich_text":
            return plain_text(value)
        case "select" | "status":
            return value.get("name") if value else None
        case "multi_select":
            return [option["name"] for option in value or []]
        case "date":
            return _decode_date(value)
        case "checkbox":
            return bool(value)
        case "number" | "url" | "email" | "phone_number":
            return value
        case "relation":
            return RelationValue(
                ids=tuple(item["id"] for item in value or []),
                has_more=bool(prop.get("has_more")),
            )
        case "created_time" | "last_edited_time":
            return parse_notion_datetime(value) if value else None
        case "formula":
            return _decode_formula(value or {})
        case "rollup":
            return _decode_rollup(value or {})
        case _:
            return value
