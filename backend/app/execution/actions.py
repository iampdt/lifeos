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
    WEEK_CLOSE = "week.close"
    MONTH_CREATE = "month.create"
    MONTH_CLOSE = "month.close"
    BOOK_UPDATE_PROGRESS = "book.update_progress"


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MarkDone(Payload):
    page_id: str
    completed_on: date


class SetDue(Payload):
    """Carry forward or reschedule. In a workspace that links tasks to weekly and monthly
    pages, those links move with the Due date (R7): each names an existing page or a
    `*.create` proposal. Without them, only the date changes."""

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
            if page is not None and proposal is not None:
                raise ValueError(f"give {period}_page_id or {period}_proposal_id, not both")
        return self


class SetStatus(Payload):
    page_id: str
    status: Literal["next", "doing", "waiting", "cancelled"]


class CreateTask(Payload):
    title: str = Field(min_length=1, max_length=200)
    status: Literal["inbox", "next"] = "inbox"
    due: date | None = None
    priority: str | None = Field(default=None, pattern=r"^p\d$")  # p0 is the highest
    area: str | None = None
    goal_ids: tuple[str, ...] = ()


class UpsertHabitDay(Payload):
    """Checks and metric values keyed by the habits mapping's field names; `build_plan` refuses
    names the workspace doesn't have."""

    day: date
    checks: dict[str, bool] = Field(default_factory=dict)
    metrics: dict[str, float] = Field(default_factory=dict)
    notes: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _legacy_metrics(cls, data: Any) -> Any:
        # Proposals made before habits were generic stored sleep and screen time as fields.
        if isinstance(data, dict) and ({"sleep", "screen_time"} & set(data)):
            data = dict(data)
            metrics = dict(data.get("metrics") or {})
            for name in ("sleep", "screen_time"):
                value = data.pop(name, None)
                if value is not None:
                    metrics[name] = value
            data["metrics"] = metrics
        return data

    @model_validator(mode="after")
    def _sane_metrics(self) -> UpsertHabitDay:
        for name, value in self.metrics.items():
            if not 0 <= value <= 100_000:
                raise ValueError(f"{name} out of range: {value}")
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


class ClosePeriod(Payload):
    """Write the review text and mark a Weekly/Monthly Progress page Closed."""

    page_id: str
    review: str = Field(min_length=1, max_length=6000)


class UpdateBookProgress(Payload):
    page_id: str
    status: Literal["reading", "finished"] | None = None
    pages_read: float | None = Field(default=None, ge=0, le=100_000)
    started: date | None = None
    finished: date | None = None

    @model_validator(mode="after")
    def _something(self) -> UpdateBookProgress:
        if all(getattr(self, f) is None for f in ("status", "pages_read", "started", "finished")):
            raise ValueError("nothing to update")
        return self


PAYLOADS: dict[ActionKind, type[Payload]] = {
    ActionKind.TASK_MARK_DONE: MarkDone,
    ActionKind.TASK_SET_DUE: SetDue,
    ActionKind.TASK_SET_STATUS: SetStatus,
    ActionKind.TASK_CREATE: CreateTask,
    ActionKind.HABIT_UPSERT_DAY: UpsertHabitDay,
    ActionKind.WEEK_CREATE: CreatePeriod,
    ActionKind.WEEK_CLOSE: ClosePeriod,
    ActionKind.MONTH_CREATE: CreatePeriod,
    ActionKind.MONTH_CLOSE: ClosePeriod,
    ActionKind.BOOK_UPDATE_PROGRESS: UpdateBookProgress,
}

DATA_SOURCE: dict[ActionKind, str] = {
    ActionKind.TASK_MARK_DONE: "tasks",
    ActionKind.TASK_SET_DUE: "tasks",
    ActionKind.TASK_SET_STATUS: "tasks",
    ActionKind.TASK_CREATE: "tasks",
    ActionKind.HABIT_UPSERT_DAY: "habits",
    ActionKind.WEEK_CREATE: "weeks",
    ActionKind.WEEK_CLOSE: "weeks",
    ActionKind.MONTH_CREATE: "months",
    ActionKind.MONTH_CLOSE: "months",
    ActionKind.BOOK_UPDATE_PROGRESS: "books",
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
        if not spec.has_value(field_name, key):
            raise ValueError(f"{source}.{field_name} has no option for {key!r} in this workspace")
        return spec.prop(field_name).value(key)

    match payload:
        case MarkDone():
            values = {"status": option("status", "done")}
            if spec.has("completed"):
                values["completed"] = payload.completed_on
            return WritePlan(source, payload.page_id, values)
        case SetDue():
            values = {"due": payload.new_due}
            for period in ("week", "month"):
                page = getattr(payload, f"{period}_page_id")
                proposal = getattr(payload, f"{period}_proposal_id")
                if (page or proposal) and spec.has(period):
                    values[period] = [page or resolved_pages[proposal]]
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
            if payload.priority and spec.has("priority"):
                values["priority"] = option("priority", payload.priority)
            if payload.area and spec.has("area"):
                values["area"] = payload.area
            if payload.goal_ids and spec.has("goal"):
                values["goal"] = list(payload.goal_ids)
            lookup = Lookup("task_title_due", day=payload.due, title=payload.title)
            return WritePlan(source, None, values, lookup)
        case UpsertHabitDay():
            unknown = (set(payload.checks) - set(spec.habit_fields)) | (
                set(payload.metrics) - set(spec.metric_fields)
            )
            if unknown:
                raise ValueError(f"not habits or metrics of this workspace: {sorted(unknown)}")
            values = {
                "title": format_habit_title(payload.day, spec.date_format),
                **payload.checks,
                **payload.metrics,
            }
            if spec.has("day"):
                values["day"] = payload.day
            if payload.notes is not None and spec.has("notes"):
                values["notes"] = payload.notes
            return WritePlan(
                source, None, values, Lookup("habit_day", day=payload.day), upsert=True
            )
        case CreatePeriod():
            values = {"title": payload.title, "start": payload.start, "end": payload.end}
            if spec.has_value("status", payload.status):
                values["status"] = option("status", payload.status)
            return WritePlan(source, None, values, Lookup("period_start", day=payload.start))
        case ClosePeriod():
            values = {}
            if spec.has("review"):
                values["review"] = payload.review
            if spec.has_value("status", "closed"):
                values["status"] = option("status", "closed")
            if not values:
                raise ValueError(f"{source} has neither a review nor a closed status to write")
            return WritePlan(source, payload.page_id, values)
        case UpdateBookProgress():
            values = {}
            if payload.status and spec.has_value("status", payload.status):
                values["status"] = option("status", payload.status)
            for name in ("pages_read", "started", "finished"):
                if getattr(payload, name) is not None and spec.has(name):
                    values[name] = getattr(payload, name)
            if not values:
                raise ValueError("this workspace's reading list has none of these fields")
            return WritePlan(source, payload.page_id, values)
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
