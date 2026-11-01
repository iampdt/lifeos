"""Action kinds: typed payloads and the Notion write each one turns into.

An LLM (or any caller) can only ask for one of these kinds, with a payload that passes
validation. `build_plan` turns a payload into a `WritePlan`: which data source, which page,
which logical fields get which values. The executor then checks the plan against the
allowlist and applies it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.notion.codecs import DateValue, RelationValue, decode_property
from app.notion.mapping import DataSourceSpec
from app.services.calendar import DEFAULT_TZ, format_habit_title, local_date


class ActionKind(StrEnum):
    TASK_MARK_DONE = "task.mark_done"
    TASK_SET_DUE = "task.set_due"
    TASK_SET_STATUS = "task.set_status"
    TASK_CREATE = "task.create"
    HABIT_UPSERT_DAY = "habit.upsert_day"
    WEEK_CREATE = "week.create"
    MONTH_CREATE = "month.create"


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MarkDone(Payload):
    page_id: str
    completed_on: date


class SetDue(Payload):
    """Carry forward or reschedule. The Week and Month links always move with the Due date
    (owner decisions Q3/Q8): each must name an existing page or a `*.create` proposal."""

    page_id: str
    new_due: date
    decision: Literal["carry_forward", "reschedule"]
    week_page_id: str | None = None
    week_proposal_id: uuid.UUID | None = None
    month_page_id: str | None = None
    month_proposal_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _links(self) -> SetDue:
        for period in ("week", "month"):
            page, proposal = (
                getattr(self, f"{period}_page_id"),
                getattr(self, f"{period}_proposal_id"),
            )
            if (page is None) == (proposal is None):
                raise ValueError(
                    f"exactly one of {period}_page_id / {period}_proposal_id is required"
                )
        return self


class SetStatus(Payload):
    page_id: str
    status: Literal["next", "doing", "waiting", "cancelled"]


class CreateTask(Payload):
    title: str = Field(min_length=1, max_length=200)
    status: Literal["inbox", "next"] = "inbox"
    due: date | None = None
    priority: Literal["p0", "p1", "p2", "p3"] | None = None
    area: str | None = None
    goal_ids: tuple[str, ...] = ()


HABIT_CHECKS = (
    "woke_on_time", "dsa", "deep_work", "technical_learning", "workout",
    "protein_target", "reading", "speech_practice", "daily_highlight",
)  # fmt: skip


class UpsertHabitDay(Payload):
    day: date
    checks: dict[str, bool] = Field(default_factory=dict)
    sleep: float | None = Field(default=None, ge=0, le=24)
    screen_time: float | None = Field(default=None, ge=0, le=1440)
    notes: str | None = None

    @model_validator(mode="after")
    def _known_checks(self) -> UpsertHabitDay:
        unknown = set(self.checks) - set(HABIT_CHECKS)
        if unknown:
            raise ValueError(f"unknown habit checks: {sorted(unknown)}")
        return self


class CreatePeriod(Payload):
    title: str = Field(min_length=1, max_length=100)
    start: date
    end: date
    status: Literal["upcoming", "current"] = "upcoming"

    @model_validator(mode="after")
    def _ordered(self) -> CreatePeriod:
        if self.end < self.start:
            raise ValueError("end must not be before start")
        return self


PAYLOADS: dict[ActionKind, type[Payload]] = {
    ActionKind.TASK_MARK_DONE: MarkDone,
    ActionKind.TASK_SET_DUE: SetDue,
    ActionKind.TASK_SET_STATUS: SetStatus,
    ActionKind.TASK_CREATE: CreateTask,
    ActionKind.HABIT_UPSERT_DAY: UpsertHabitDay,
    ActionKind.WEEK_CREATE: CreatePeriod,
    ActionKind.MONTH_CREATE: CreatePeriod,
}

DATA_SOURCE: dict[ActionKind, str] = {
    ActionKind.TASK_MARK_DONE: "tasks",
    ActionKind.TASK_SET_DUE: "tasks",
    ActionKind.TASK_SET_STATUS: "tasks",
    ActionKind.TASK_CREATE: "tasks",
    ActionKind.HABIT_UPSERT_DAY: "habits",
    ActionKind.WEEK_CREATE: "weeks",
    ActionKind.MONTH_CREATE: "months",
}


def parse_payload(kind: ActionKind | str, data: dict[str, Any]) -> Payload:
    return PAYLOADS[ActionKind(kind)].model_validate(data)


def target_page_id(payload: Payload) -> str | None:
    return getattr(payload, "page_id", None)


def importance(kind: ActionKind, payload: Payload) -> str:
    if isinstance(payload, SetStatus) and payload.status == "cancelled":
        return "important"
    return "normal"


def dependencies(payload: Payload) -> list[uuid.UUID]:
    if isinstance(payload, SetDue):
        return [p for p in (payload.week_proposal_id, payload.month_proposal_id) if p]
    return []


@dataclass(frozen=True)
class Lookup:
    """How to find an existing page before creating one (find-or-create)."""

    kind: Literal["habit_day", "period_start", "task_title_due"]
    day: date | None = None
    title: str | None = None


@dataclass(frozen=True)
class WritePlan:
    data_source: str
    page_id: str | None  # None means create (after a lookup)
    values: dict[str, Any] = field(default_factory=dict)  # logical field → Python value
    lookup: Lookup | None = None
    upsert: bool = False  # True: update a found page; False: reuse a found page unchanged


def build_plan(
    kind: ActionKind | str,
    payload: Payload,
    specs: dict[str, DataSourceSpec],
    resolved_pages: dict[uuid.UUID, str] | None = None,
) -> WritePlan:
    """Translate a payload into concrete field values. `resolved_pages` maps proposal IDs of
    executed dependencies to the page they created."""
    kind = ActionKind(kind)
    resolved_pages = resolved_pages or {}
    source = DATA_SOURCE[kind]
    spec = specs[source]

    def option(field_name: str, key: str) -> str:
        return spec.prop(field_name).value(key)

    match payload:
        case MarkDone():
            values = {"status": option("status", "done"), "completed": payload.completed_on}
            return WritePlan(source, payload.page_id, values)
        case SetDue():
            week = payload.week_page_id or resolved_pages[payload.week_proposal_id]
            month = payload.month_page_id or resolved_pages[payload.month_proposal_id]
            values = {"due": payload.new_due, "week": [week], "month": [month]}
            return WritePlan(source, payload.page_id, values)
        case SetStatus():
            return WritePlan(source, payload.page_id, {"status": option("status", payload.status)})
        case CreateTask():
            values: dict[str, Any] = {
                "title": payload.title,
                "status": option("status", payload.status),
            }
            if payload.due:
                values["due"] = payload.due
            if payload.priority:
                values["priority"] = option("priority", payload.priority)
            if payload.area:
                values["area"] = payload.area
            if payload.goal_ids:
                values["goal"] = list(payload.goal_ids)
            lookup = Lookup("task_title_due", day=payload.due, title=payload.title)
            return WritePlan(source, None, values, lookup)
        case UpsertHabitDay():
            values = {"title": format_habit_title(payload.day), **payload.checks}
            for name in ("sleep", "screen_time", "notes"):
                if getattr(payload, name) is not None:
                    values[name] = getattr(payload, name)
            return WritePlan(
                source, None, values, Lookup("habit_day", day=payload.day), upsert=True
            )
        case CreatePeriod():
            values = {
                "title": payload.title,
                "start": payload.start,
                "end": payload.end,
                "status": option("status", payload.status),
            }
            return WritePlan(source, None, values, Lookup("period_start", day=payload.start))
    raise ValueError(f"no plan for {kind}")  # pragma: no cover


# --- comparing values (stale detection and read-after-write verification) ---------------


def _canonical_id(page_id: str) -> str:
    return page_id.replace("-", "").lower()


def normalize(value: Any) -> Any:
    """JSON-friendly form used to compare intended values with what Notion returns."""
    if isinstance(value, DateValue):
        value = value.start
    if isinstance(value, date):
        return local_date(value, DEFAULT_TZ).isoformat()
    if isinstance(value, RelationValue):
        return sorted(_canonical_id(i) for i in value.ids)
    if isinstance(value, list | tuple):
        return sorted(_canonical_id(v) if isinstance(v, str) else v for v in value)
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def read_fields(spec: DataSourceSpec, page: dict[str, Any], fields: list[str]) -> dict[str, Any]:
    properties = page.get("properties", {})
    out: dict[str, Any] = {}
    for name in fields:
        raw = properties.get(spec.prop(name).name)
        decoded = decode_property(raw) if raw else None
        if spec.prop(name).type == "relation" and decoded is None:
            decoded = RelationValue(ids=())
        out[name] = normalize(decoded)
    return out


def expected_fields(values: dict[str, Any]) -> dict[str, Any]:
    return {name: normalize(value) for name, value in values.items()}
