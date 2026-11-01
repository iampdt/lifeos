"""Read-only repositories: Notion pages in, domain models out.

Each repository knows one data source through its `DataSourceSpec`, so it never hard-codes a
Notion display name. Write methods are added in Phase 4, and only the executor may call them.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from zoneinfo import ZoneInfo

from app.domain.models import Book, Goal, HabitDay, PeriodPage, Task
from app.notion.client import NotionClient
from app.notion.codecs import DateValue, RelationValue, decode_property, parse_notion_datetime
from app.notion.mapping import DataSourceSpec
from app.notion.queries import QuerySpec, TaskQueries
from app.services.calendar import DEFAULT_TZ, local_date, parse_habit_title

Page = dict[str, Any]


class Repository:
    def __init__(self, client: NotionClient, spec: DataSourceSpec, tz: ZoneInfo = DEFAULT_TZ):
        if not spec.data_source_id:
            raise ValueError(f"{spec.logical_name}: no data-source ID configured ({spec.env})")
        self.client = client
        self.spec = spec
        self.tz = tz

    # --- helpers ---------------------------------------------------------------------------

    def _raw(self, page: Page, field: str) -> dict[str, Any] | None:
        return page.get("properties", {}).get(self.spec.prop(field).name)

    def _value(self, page: Page, field: str) -> Any:
        if field not in self.spec.properties:
            return None
        raw = self._raw(page, field)
        return decode_property(raw) if raw else None

    def _text(self, page: Page, field: str) -> str:
        value = self._value(page, field)
        return value if isinstance(value, str) else ""

    def _date(self, page: Page, field: str) -> date | None:
        value = self._value(page, field)
        return local_date(value.start, self.tz) if isinstance(value, DateValue) else None

    async def _relation(self, page: Page, field: str) -> tuple[str, ...]:
        value = self._value(page, field)
        if not isinstance(value, RelationValue):
            return ()
        if value.has_more:  # the page object truncates long relations
            raw = self._raw(page, field) or {}
            return tuple(await self.client.get_relation_ids(page["id"], raw["id"]))
        return value.ids

    def _base(self, page: Page) -> dict[str, Any]:
        return {
            "id": page["id"],
            "url": page.get("url", ""),
            "title": self._text(page, "title"),
            "last_edited_time": parse_notion_datetime(page["last_edited_time"]),
        }

    async def _pages(self, query: QuerySpec | None = None) -> list[Page]:
        query = query or QuerySpec()
        return [
            page
            async for page in self.client.query_data_source(
                self.spec.data_source_id, filter=query.filter, sorts=query.sorts or None
            )
            if page.get("object") == "page" and not page.get("in_trash")
        ]


class TaskRepository(Repository):
    def __init__(self, client: NotionClient, spec: DataSourceSpec, tz: ZoneInfo = DEFAULT_TZ):
        super().__init__(client, spec, tz)
        self.views = TaskQueries(spec)

    async def to_domain(self, page: Page) -> Task:
        week_key = self._value(page, "week_key")
        month_key = self._value(page, "month_key")
        created = self._value(page, "created")
        return Task(
            **self._base(page),
            status=self._value(page, "status"),
            priority=self._value(page, "priority"),
            area=self._value(page, "area"),
            due=self._date(page, "due"),
            completed=self._date(page, "completed"),
            recurring=bool(self._value(page, "recurring")),
            goal_ids=await self._relation(page, "goal"),
            week_ids=await self._relation(page, "week"),
            month_ids=await self._relation(page, "month"),
            # Keep a formula value only if it's text: the live Month Key formula returns a
            # boolean, so it carries no information (LifeOS computes keys from Due anyway).
            week_key=week_key if isinstance(week_key, str) and week_key else None,
            month_key=month_key if isinstance(month_key, str) and month_key else None,
            created_time=created,
        )

    async def query(self, query: QuerySpec) -> list[Task]:
        return [await self.to_domain(page) for page in await self._pages(query)]

    async def active(self) -> list[Task]:
        return await self.query(self.views.active())

    async def today(self, today: date) -> list[Task]:
        return await self.query(self.views.today(today))

    async def overdue(self, today: date) -> list[Task]:
        return await self.query(self.views.overdue(today))

    async def inbox(self) -> list[Task]:
        return await self.query(self.views.inbox())

    async def next_7_days(self, today: date) -> list[Task]:
        return await self.query(self.views.next_7_days(today))

    async def done_due_between(self, start: date, end: date) -> list[Task]:
        return await self.query(self.views.done_due_between(start, end))

    async def due_between(self, start: date, end: date) -> list[Task]:
        return await self.query(self.views.due_between(start, end))

    async def all(self) -> list[Task]:
        return await self.query(QuerySpec())

    async def get(self, page_id: str) -> Task:
        return await self.to_domain(await self.client.get_page(page_id))


class GoalRepository(Repository):
    def to_domain(self, page: Page) -> Goal:
        return Goal(
            **self._base(page),
            type=self._value(page, "type"),
            status=self._value(page, "status"),
            area=self._value(page, "area"),
            priority=self._value(page, "priority"),
            current=self._value(page, "current"),
            target=self._value(page, "target"),
            success_metric=self._text(page, "success_metric"),
            start=self._date(page, "start"),
            deadline=self._date(page, "deadline"),
        )

    async def active(self) -> list[Goal]:
        status = self.spec.prop("status")
        query = QuerySpec(
            {"property": status.name, status.type: {"equals": status.value("active")}}
        )
        return [self.to_domain(page) for page in await self._pages(query)]

    async def all(self) -> list[Goal]:
        return [self.to_domain(page) for page in await self._pages()]


class HabitRepository(Repository):
    CHECK_FIELDS = (
        "woke_on_time",
        "dsa",
        "deep_work",
        "technical_learning",
        "workout",
        "protein_target",
        "reading",
        "speech_practice",
        "daily_highlight",
    )

    def to_domain(self, page: Page) -> HabitDay:
        title = self._text(page, "title")
        return HabitDay(
            **self._base(page),
            day=parse_habit_title(title),
            checks={
                f: bool(self._value(page, f))
                for f in self.CHECK_FIELDS
                if f in self.spec.properties
            },
            sleep=self._value(page, "sleep"),
            screen_time=self._value(page, "screen_time"),
            notes=self._text(page, "notes"),
        )

    async def all(self) -> list[HabitDay]:
        # Rows are keyed by a text title, which Notion can't filter as a date, so the
        # repository reads every row and parses titles. Fine at one row per day.
        return [self.to_domain(page) for page in await self._pages()]

    async def for_day(self, day: date) -> list[HabitDay]:
        """All rows whose title parses to `day` (normally 0 or 1; more means a duplicate)."""
        return [row for row in await self.all() if row.day == day]


class PeriodRepository(Repository):
    """Weekly Progress or Monthly Progress."""

    async def to_domain(self, page: Page) -> PeriodPage:
        return PeriodPage(
            **self._base(page),
            start=self._date(page, "start"),
            end=self._date(page, "end"),
            status=self._value(page, "status"),
            theme=self._text(page, "theme"),
            review=self._text(page, "review"),
            task_ids=await self._relation(page, "tasks"),
        )

    async def all(self) -> list[PeriodPage]:
        query = QuerySpec(
            sorts=[{"property": self.spec.prop("start").name, "direction": "ascending"}]
        )
        return [await self.to_domain(page) for page in await self._pages(query)]

    async def starting_on(self, start: date) -> list[PeriodPage]:
        prop = self.spec.prop("start")
        query = QuerySpec({"property": prop.name, "date": {"equals": start.isoformat()}})
        return [await self.to_domain(page) for page in await self._pages(query)]

    async def covering(self, day: date) -> list[PeriodPage]:
        """Pages whose Start–End range contains `day`, latest start first. Older pages don't
        always run Monday–Sunday, so this finds them where `starting_on` wouldn't."""
        start, end = self.spec.prop("start"), self.spec.prop("end")
        query = QuerySpec(
            {
                "and": [
                    {"property": start.name, "date": {"on_or_before": day.isoformat()}},
                    {"property": end.name, "date": {"on_or_after": day.isoformat()}},
                ]
            }
        )
        pages = [await self.to_domain(page) for page in await self._pages(query)]
        return sorted(pages, key=lambda p: p.start or date.min, reverse=True)


class BookRepository(Repository):
    def to_domain(self, page: Page) -> Book:
        shelves = self._value(page, "shelf") or []
        return Book(
            **self._base(page),
            author=self._text(page, "author"),
            status=self._value(page, "status"),
            lane=self._value(page, "lane"),
            up_next=self._value(page, "up_next"),
            area=self._value(page, "area"),
            shelves=tuple(shelves),
            pages=self._value(page, "pages"),
            pages_read=self._value(page, "pages_read"),
            started=self._date(page, "started"),
            finished=self._date(page, "finished"),
        )

    async def queue(self) -> list[Book]:
        """Books with a Status (the reading queue); library-only books have none."""
        status = self.spec.prop("status")
        query = QuerySpec({"property": status.name, status.type: {"is_not_empty": True}})
        return [self.to_domain(page) for page in await self._pages(query)]

    async def all(self) -> list[Book]:
        return [self.to_domain(page) for page in await self._pages()]
