"""The demo workspace: a believable week for a software engineer, built relative to today.

Everything here is invented. Dates are computed from today, so the demo always shows tasks due
today, a couple overdue (for the weekly review), work done earlier in the week, Inbox items to
triage, a few days of habit scorecards and books in progress.
"""

from __future__ import annotations

from datetime import date, timedelta

from app.demo.notion import InMemoryNotion, schema_for
from app.notion.mapping import DataSourceSpec
from app.services.calendar import (
    format_habit_title,
    month_range,
    month_title,
    week_range,
    week_title,
)

DEMO_DATA_SOURCES = {
    "tasks": "demo-tasks",
    "goals": "demo-goals",
    "habits": "demo-habits",
    "weeks": "demo-weeks",
    "months": "demo-months",
    "books": "demo-books",
}
DEMO_ENV = {f"NOTION_DS_{name.upper()}": ds_id for name, ds_id in DEMO_DATA_SOURCES.items()}

HABITS = [
    {"woke_on_time": True, "dsa": True, "deep_work": True, "workout": True, "reading": True},
    {"woke_on_time": True, "dsa": False, "deep_work": True, "technical_learning": True},
    {"woke_on_time": False, "dsa": True, "workout": True, "protein_target": True},
    {
        "woke_on_time": True,
        "dsa": True,
        "deep_work": True,
        "reading": True,
        "daily_highlight": True,
    },
    {"woke_on_time": True, "deep_work": False, "workout": True, "speech_practice": True},
    {"woke_on_time": True, "dsa": True, "technical_learning": True, "reading": True},
]


def seed_workspace(notion: InMemoryNotion, specs: dict[str, DataSourceSpec], today: date) -> None:
    notion.clear()
    notion.schemas = {spec.data_source_id: schema_for(spec) for spec in specs.values()}
    day = lambda offset: today + timedelta(days=offset)  # noqa: E731
    week_start, week_end = week_range(today)
    month_start, month_end = month_range(today)

    notion.seed(specs["weeks"], {
        "title": week_title(week_start - timedelta(days=7)),
        "start": week_start - timedelta(days=7), "end": week_start - timedelta(days=1),
        "status": "Closed",
        "review": "Shipped the notification worker refactor; interview prep slipped twice.",
    })  # fmt: skip
    notion.seed(specs["weeks"], {"title": week_title(today), "start": week_start,
                                 "end": week_end, "status": "Current",
                                 "theme": "Interview prep and a clean sprint demo"})  # fmt: skip
    notion.seed(specs["months"], {"title": month_title(today), "start": month_start,
                                  "end": month_end, "status": "Current"})  # fmt: skip

    for title, kind, current, target in [
        ("Land a backend engineering role", "Year", 3, 10),
        ("Ship the sprint demo with zero open bugs", "Week", None, None),
        ("Read 12 books this year", "Year", 9, 12),
    ]:
        notion.seed(specs["goals"], {"title": title, "type": kind, "status": "Active",
                                     "current": current, "target": target})  # fmt: skip

    tasks = [
        # Due today: the three major tasks, plus a recurring one that doesn't count toward R1.
        ("Mock system design interview", "Next", "P0", day(0), "Career Switch"),
        ("Write unit tests for the payments service", "Doing", "P1", day(0), "Work"),
        ("Read DDIA chapter 5", "Next", "P2", day(0), "Learning"),
        # Overdue: these wait for the weekly review.
        ("Update resume summary", "Next", "P1", day(-2), "Career Switch"),
        ("Book dentist appointment", "Next", "P3", day(-4), "Personal"),
        # Coming up.
        ("Apply to two backend roles", "Next", "P0", day(1), "Career Switch"),
        ("Submit conference talk proposal", "Next", "P1", day(2), "Career Switch"),
        ("Prepare sprint demo", "Next", "P1", day(3), "Work"),
        # Backlog the nightly plan can pull from.
        ("Write LLD notes for a parking lot system", "Next", "P1", None, "Learning"),
        ("Clean up old feature flags", "Next", "P2", None, "Work"),
        # Inbox, triaged in the weekly review.
        ("Try the new Postgres 18 features", "Inbox", None, None, "Learning"),
        ("Renew passport", "Inbox", None, None, "Personal"),
    ]
    for title, status, priority, due, area in tasks:
        notion.seed(specs["tasks"], {"title": title, "status": status, "priority": priority,
                                     "due": due, "area": area})  # fmt: skip
    notion.seed(specs["tasks"], {"title": "Daily standup", "status": "Next", "due": day(0),
                                 "recurring": True, "area": "Work"})  # fmt: skip
    for title, area in [("Refactor notification worker", "Work"),
                        ("Solve 3 graph problems", "Learning")]:  # fmt: skip
        done = {"title": title, "status": "Done", "priority": "P1", "area": area}
        notion.seed(specs["tasks"], {**done, "due": week_start, "completed": week_start})

    for offset, checks in zip(range(-6, 0), HABITS, strict=True):
        values = {"title": format_habit_title(day(offset), specs["habits"].date_format), **checks,
                  "sleep": 7.5 if checks.get("woke_on_time") else 6, "screen_time": 3}  # fmt: skip
        notion.seed(specs["habits"], values)

    notion.seed(specs["books"], {"title": "Designing Data-Intensive Applications",
                                 "author": "Martin Kleppmann", "status": "Reading",
                                 "pages": 590, "pages_read": 210, "started": day(-20)})  # fmt: skip
    notion.seed(specs["books"], {"title": "The Pragmatic Programmer",
                                 "author": "David Thomas and Andrew Hunt", "status": "Reading",
                                 "pages": 352, "pages_read": 120, "started": day(-9)})  # fmt: skip
    notion.seed(specs["books"], {"title": "Clean Architecture", "author": "Robert C. Martin",
                                 "status": "Want to Read", "pages": 432})  # fmt: skip
