"""Encode Python values into Notion property-write JSON (the inverse of `codecs.py`)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Any

from app.notion.mapping import PropertySpec

# Notion rejects rich-text objects longer than 2,000 characters, so long text is split.
MAX_TEXT_CHUNK = 2000


def _text_objects(text: str) -> list[dict[str, Any]]:
    chunks = [text[i : i + MAX_TEXT_CHUNK] for i in range(0, len(text), MAX_TEXT_CHUNK)]
    return [{"type": "text", "text": {"content": chunk}} for chunk in chunks]


class NotWritable(ValueError):
    """The property type can't be written (formula, rollup, created_time...)."""


def encode_value(spec: PropertySpec, value: Any) -> dict[str, Any]:
    match spec.type:
        case "title":
            return {"title": _text_objects(str(value or ""))}
        case "rich_text":
            return {"rich_text": _text_objects(str(value or ""))}
        case "select" | "status":
            return {spec.type: {"name": value} if value else None}
        case "multi_select":
            return {"multi_select": [{"name": name} for name in value or []]}
        case "date":
            if value is None:
                return {"date": None}
            if not isinstance(value, date):
                raise TypeError(f"{spec.name}: expected a date, got {type(value).__name__}")
            return {"date": {"start": value.isoformat()}}
        case "checkbox":
            return {"checkbox": bool(value)}
        case "number":
            return {"number": value}
        case "url":
            return {"url": value or None}
        case "relation":
            ids: Sequence[str] = value or []
            return {"relation": [{"id": page_id} for page_id in ids]}
        case _:
            raise NotWritable(f"{spec.name}: properties of type {spec.type!r} can't be written")


def encode_properties(specs: dict[str, PropertySpec], values: dict[str, Any]) -> dict[str, Any]:
    """Logical field → value becomes Notion display name → property JSON."""
    return {specs[field].name: encode_value(specs[field], value) for field, value in values.items()}
