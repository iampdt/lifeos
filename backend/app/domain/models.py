"""Domain models: what the rest of LifeOS sees instead of raw Notion JSON."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class NotionRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    url: str
    title: str
    last_edited_time: datetime


class Task(NotionRecord):
    status: str | None = None
    priority: str | None = None
    area: str | None = None
    due: date | None = None
    completed: date | None = None
    recurring: bool = False
    goal_ids: tuple[str, ...] = ()
    week_ids: tuple[str, ...] = ()
    month_ids: tuple[str, ...] = ()
    # Values of the Notion formulas, kept so LifeOS can compare them with its own calendar rules.
    week_key: str | None = None
    month_key: str | None = None
    created_time: datetime | None = None


class Goal(NotionRecord):
    type: str | None = None
    status: str | None = None
    area: str | None = None
    priority: str | None = None
    current: float | None = None
    target: float | None = None
    success_metric: str = ""
    start: date | None = None
    deadline: date | None = None


class HabitDay(NotionRecord):
    day: date | None = None  # parsed from the title; None if the title isn't a date
    checks: dict[str, bool] = Field(default_factory=dict)
    sleep: float | None = None
    screen_time: float | None = None
    notes: str = ""


class PeriodPage(NotionRecord):
    """A Weekly Progress or Monthly Progress page."""

    start: date | None = None
    end: date | None = None
    status: str | None = None
    theme: str = ""
    review: str = ""
    task_ids: tuple[str, ...] = ()


class Book(NotionRecord):
    author: str = ""
    status: str | None = None
    lane: str | None = None
    up_next: float | None = None
    area: str | None = None
    shelves: tuple[str, ...] = ()
    pages: float | None = None
    pages_read: float | None = None
    started: date | None = None
    finished: date | None = None
