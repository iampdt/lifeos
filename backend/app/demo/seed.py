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

    goals = {}
    for key, title, kind, current, target, metric in [
        ("role", "Land a backend engineering role", "Year", 3, 10,
         "Milestones from resume to signed offer"),
        ("demo", "Ship the sprint demo with zero open bugs", "Week", None, None, ""),
        ("books", "Read 12 books this year", "Year", 9, 12, "Books finished"),
    ]:  # fmt: skip
        goals[key] = notion.seed(specs["goals"], {"title": title, "type": kind, "status": "Active",
                                                  "current": current, "target": target,
                                                  "success_metric": metric})  # fmt: skip

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
    linked = {
        "Mock system design interview": "role", "Update resume summary": "role",
        "Apply to two backend roles": "role", "Write unit tests for the payments service": "demo",
        "Prepare sprint demo": "demo", "Read DDIA chapter 5": "books",
    }  # fmt: skip
    for title, status, priority, due, area in tasks:
        goal = [goals[linked[title]]] if title in linked else []
        notion.seed(specs["tasks"], {"title": title, "status": status, "priority": priority,
                                     "due": due, "area": area, "goal": goal})  # fmt: skip
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

    for title, blocks in DOCUMENTS:
        notion.add_document(title, blocks)


# Standalone pages for the knowledge index: what someone might keep next to their tasks.
DOCUMENTS: list[tuple[str, list[tuple[str, str]]]] = [
    ("Interview prep plan", [
        ("paragraph", "Goal: a backend engineering offer by the end of the year. Two mock "
                      "interviews a week until then, alternating system design and coding."),
        ("heading_2", "System design"),
        ("bulleted_list_item", "Practise one design a week out loud, 45 minutes, then write "
                               "down what I missed."),
        ("bulleted_list_item", "Always cover: requirements, capacity estimates, API, data "
                               "model, scaling, failure modes."),
        ("bulleted_list_item", "Weak spots so far: consistency trade-offs and back-of-the-"
                               "envelope numbers."),
        ("heading_2", "Coding"),
        ("bulleted_list_item", "Three graph or dynamic programming problems on weekdays, "
                               "timed to 30 minutes each."),
        ("bulleted_list_item", "Explain the approach before writing code; state complexity "
                               "at the end."),
        ("heading_2", "Applications"),
        ("paragraph", "Apply to two roles a week, only where the stack is Python or Go. "
                      "Tailor the resume summary to each posting."),
    ]),
    ("Payments service onboarding", [
        ("paragraph", "Notes from joining the payments team."),
        ("heading_2", "Architecture"),
        ("paragraph", "The payments service is a FastAPI app in front of Postgres. Card "
                      "charges go through an outbox table and a worker, so a crash never "
                      "charges twice: every charge has an idempotency key."),
        ("heading_2", "On call"),
        ("bulleted_list_item", "Alerts page the primary for failed charges above 2% for five "
                               "minutes."),
        ("bulleted_list_item", "First step for stuck charges: check the outbox worker's lag "
                               "dashboard, then restart the worker."),
        ("heading_2", "Testing"),
        ("paragraph", "Unit tests use a fake card gateway; the nightly job runs the "
                      "contract tests against the gateway's sandbox."),
    ]),
    ("Reading notes: Designing Data-Intensive Applications", [
        ("heading_2", "Chapter 5: Replication"),
        ("paragraph", "Single-leader replication is simple but the leader is a bottleneck "
                      "for writes. Asynchronous followers can lag, which breaks read-your-"
                      "writes unless reads of your own data go to the leader."),
        ("paragraph", "Multi-leader and leaderless setups accept writes in several places "
                      "and need conflict resolution: last write wins loses data, so prefer "
                      "merging or asking the user."),
        ("quote", "Replication lag is the source of most surprising bugs in distributed "
                  "systems."),
    ]),
]  # fmt: skip
