"""Builders for Notion API JSON shaped like real responses (API version 2025-09-03).

They are synthetic, not recordings of the owner's workspace, so no personal data enters the repo.
"""

from __future__ import annotations

from typing import Any

from app.core.config import BACKEND_ROOT
from app.demo.notion import schema_for
from app.notion.mapping import DataSourceSpec, load_mapping

MAPPING_PATH = BACKEND_ROOT / "config" / "notion_mapping.yaml"

FAKE_ENV = {
    "NOTION_DS_TASKS": "ds-tasks",
    "NOTION_DB_TASKS": "db-tasks",
    "NOTION_DS_GOALS": "ds-goals",
    "NOTION_DS_HABITS": "ds-habits",
    "NOTION_DS_WEEKS": "ds-weeks",
    "NOTION_DS_MONTHS": "ds-months",
    "NOTION_DS_BOOKS": "ds-books",
}


def specs() -> dict[str, DataSourceSpec]:
    return load_mapping(MAPPING_PATH, FAKE_ENV, "live")


def rich(text: str) -> list[dict[str, Any]]:
    return [{"type": "text", "text": {"content": text}, "plain_text": text}]


def select(name: str | None) -> dict[str, Any] | None:
    return {"id": "opt", "name": name, "color": "default"} if name else None


def date_value(start: str | None) -> dict[str, Any] | None:
    return {"start": start, "end": None, "time_zone": None} if start else None


def relation(*ids: str, has_more: bool = False) -> dict[str, Any]:
    return {"type": "relation", "relation": [{"id": i} for i in ids], "has_more": has_more}


def formula_string(value: str | None) -> dict[str, Any]:
    return {"type": "formula", "formula": {"type": "string", "string": value}}


def page(
    page_id: str, properties: dict[str, Any], edited: str = "2026-10-29T18:35:37.000Z"
) -> dict:
    return {
        "object": "page",
        "id": page_id,
        "url": f"https://www.notion.so/{page_id}",
        "in_trash": False,
        "created_time": "2026-10-27T10:55:24.000Z",
        "last_edited_time": edited,
        "parent": {"type": "data_source_id", "data_source_id": "ds-tasks"},
        "properties": properties,
    }


def task_page(
    page_id: str = "task-1",
    *,
    title: str = "Write tests",
    status: str | None = "Next",
    priority: str | None = "P1",
    due: str | None = "2026-11-03",
    completed: str | None = None,
    recurring: bool = False,
    goals: tuple[str, ...] = ("goal-1",),
    weeks: tuple[str, ...] = (),
    week_key: str | None = "2026-W45",
    month_key: str | None = "2026-11",
    goal_has_more: bool = False,
) -> dict[str, Any]:
    return page(
        page_id,
        {
            "Task": {"id": "title", "type": "title", "title": rich(title)},
            "Status": {"id": "st", "type": "select", "select": select(status)},
            "Priority": {"id": "pr", "type": "select", "select": select(priority)},
            "Due": {"id": "du", "type": "date", "date": date_value(due)},
            "Completed": {"id": "co", "type": "date", "date": date_value(completed)},
            "Area": {"id": "ar", "type": "select", "select": select("Career Switch")},
            "Estimated Time": {"id": "et", "type": "select", "select": None},
            "Energy": {"id": "en", "type": "select", "select": None},
            "Recurring": {"id": "re", "type": "checkbox", "checkbox": recurring},
            "Created": {
                "id": "cr",
                "type": "created_time",
                "created_time": "2026-10-27T10:55:24.000Z",
            },
            "Goal": {"id": "go", **relation(*goals, has_more=goal_has_more)},
            "Career Evidence": {"id": "ce", **relation()},
            "Week": {"id": "we", **relation(*weeks)},
            "Month": {"id": "mo", **relation()},
            "Week Key": {"id": "wk", **formula_string(week_key)},
            "Month Key": {"id": "mk", **formula_string(month_key)},
        },
    )


def data_source_schema(spec: DataSourceSpec) -> dict[str, Any]:
    """A data-source object whose properties exactly match the mapping."""
    return schema_for(spec)
