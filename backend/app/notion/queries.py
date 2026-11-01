"""Notion query filters that mirror the owner's saved views.

The public API queries a data source with filters but can't run a saved view, so each view the
workflows depend on is rebuilt here. Each method names the view it mirrors; Phase 3 checks them
against the real views.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from app.notion.mapping import DataSourceSpec, PropertySpec


@dataclass(frozen=True)
class QuerySpec:
    filter: dict[str, Any] | None = None
    sorts: list[dict[str, Any]] = field(default_factory=list)


def _all(*conditions: dict[str, Any]) -> dict[str, Any]:
    return conditions[0] if len(conditions) == 1 else {"and": list(conditions)}


def _select(prop: PropertySpec, operator: str, value: str) -> dict[str, Any]:
    # `select` and `status` properties use different filter keys.
    return {"property": prop.name, prop.type: {operator: value}}


def _date(prop: PropertySpec, operator: str, value: date) -> dict[str, Any]:
    return {"property": prop.name, "date": {operator: value.isoformat()}}


class TaskQueries:
    def __init__(self, spec: DataSourceSpec) -> None:
        self.status = spec.prop("status")
        self.due = spec.prop("due")
        self.priority = spec.prop("priority")
        self.created = spec.prop("created")

    def _open(self) -> list[dict[str, Any]]:
        return [
            _select(self.status, "does_not_equal", self.status.value("done")),
            _select(self.status, "does_not_equal", self.status.value("cancelled")),
        ]

    def active(self) -> QuerySpec:
        """🔥 All Active Tasks."""
        return QuerySpec(
            _all(*self._open()), [{"property": self.due.name, "direction": "ascending"}]
        )

    def today(self, today: date) -> QuerySpec:
        """🔥 Today: open tasks due today or earlier."""
        return QuerySpec(
            _all(*self._open(), _date(self.due, "on_or_before", today)),
            [
                {"property": self.priority.name, "direction": "ascending"},
                {"property": self.due.name, "direction": "ascending"},
            ],
        )

    def overdue(self, today: date) -> QuerySpec:
        """🔁 Overdue / Carry Forward: open tasks due before today."""
        return QuerySpec(
            _all(*self._open(), _date(self.due, "before", today)),
            [
                {"property": self.priority.name, "direction": "ascending"},
                {"property": self.due.name, "direction": "ascending"},
            ],
        )

    def inbox(self) -> QuerySpec:
        """📥 Inbox."""
        return QuerySpec(
            _select(self.status, "equals", self.status.value("inbox")),
            [{"property": self.created.name, "direction": "descending"}],
        )

    def next_7_days(self, today: date) -> QuerySpec:
        """📅 Next 7 Days."""
        return QuerySpec(
            _all(
                *self._open(),
                _date(self.due, "on_or_after", today),
                _date(self.due, "on_or_before", today + timedelta(days=7)),
            ),
            [{"property": self.due.name, "direction": "ascending"}],
        )

    def add_for_today(self, today: date) -> QuerySpec:
        """➕ Add for Today: Next tasks due exactly today."""
        return QuerySpec(
            _all(
                _select(self.status, "equals", self.status.value("next")),
                _date(self.due, "equals", today),
            ),
            [{"property": self.priority.name, "direction": "ascending"}],
        )

    def done_due_between(self, start: date, end: date) -> QuerySpec:
        """🏆 Done history, limited to a Due-date range (a week or a month)."""
        return QuerySpec(
            _all(
                _select(self.status, "equals", self.status.value("done")),
                _date(self.due, "on_or_after", start),
                _date(self.due, "on_or_before", end),
            ),
            [{"property": self.due.name, "direction": "descending"}],
        )

    def due_between(self, start: date, end: date) -> QuerySpec:
        """Every task with Due in the range, whatever its status (weekly metrics, R5)."""
        return QuerySpec(
            _all(_date(self.due, "on_or_after", start), _date(self.due, "on_or_before", end)),
            [{"property": self.due.name, "direction": "ascending"}],
        )
