"""Find the Weekly/Monthly Progress page for a date, or describe the page to create (rule R7).

A task moved to a new Due date takes its Week and Month links with it. When the target page
doesn't exist yet, the workflow proposes `week.create` / `month.create` and the `task.set_due`
proposal depends on it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

from app.execution.actions import CreatePeriod
from app.notion.repositories import PeriodRepository
from app.services.calendar import month_range, month_title, week_range, week_title

PeriodKind = Literal["week", "month"]


@dataclass(frozen=True)
class PeriodTarget:
    kind: PeriodKind
    start: date
    end: date
    page_id: str | None = None  # existing page
    create: CreatePeriod | None = None  # page to create when none exists
    review: str = ""  # existing Review text (used when closing the period)

    @property
    def key(self) -> tuple[str, str]:
        return self.kind, self.start.isoformat()


class PeriodResolver:
    def __init__(self, weeks: PeriodRepository, months: PeriodRepository, today: date) -> None:
        self._repos = {"week": weeks, "month": months}
        self.today = today
        self._cache: dict[tuple[PeriodKind, date], PeriodTarget] = {}

    async def resolve(self, kind: PeriodKind, day: date) -> PeriodTarget:
        start, end = week_range(day) if kind == "week" else month_range(day)
        cached = self._cache.get((kind, start))
        if cached:
            return cached
        pages = await self._repos[kind].covering(day)
        if pages:
            page = pages[0]
            target = PeriodTarget(kind, start, end, page_id=page.id, review=page.review)
        else:
            title = week_title(day) if kind == "week" else month_title(day)
            status = "current" if start <= self.today <= end else "upcoming"
            create = CreatePeriod(title=title, start=start, end=end, status=status)
            target = PeriodTarget(kind, start, end, create=create)
        self._cache[(kind, start)] = target
        return target
