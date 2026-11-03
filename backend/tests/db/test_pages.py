"""Goals, reading and day-by-day history for the web app's pages."""

import uuid
from datetime import UTC, date, datetime, timedelta

import httpx

from app.api.main import create_app
from app.core.config import Settings
from app.db.models import AgentRun
from tests.fake_notion import FakeNotion
from tests.workflow_support import OWNER_ID, SPECS, add_user, make_services, seed_task

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)  # 17:30 in Asia/Kolkata
AUTH = {"Authorization": "Bearer tok"}


def client_for(services) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=create_app(services))
    return httpx.AsyncClient(transport=transport, base_url="https://t")


def services_for(session_factory, fake):
    settings = Settings(_env_file=None, lifeos_api_token="tok")
    return make_services(session_factory, fake, None, now=NOW, settings=settings)


async def test_goals_come_with_progress_state_and_their_open_tasks(session_factory):
    fake = FakeNotion()
    launch = fake.seed(SPECS["goals"], {"title": "Launch v1", "status": "Active", "current": 3,
                                        "target": 10, "deadline": date(2026, 12, 31)})  # fmt: skip
    fake.seed(SPECS["goals"], {"title": "Old idea", "status": "Dropped"})
    fake.seed(SPECS["goals"], {"title": "Next year", "status": "Planned", "current": 12,
                               "target": 10})  # fmt: skip
    seed_task(fake, "Write the launch post", goal=[launch], due=date(2026, 11, 5))
    seed_task(fake, "Fix the signup bug", goal=[launch], due=date(2026, 11, 4))
    seed_task(fake, "Shipped already", goal=[launch], status="Done")
    seed_task(fake, "Unrelated", due=date(2026, 11, 4))
    services, _ = services_for(session_factory, fake)
    async with client_for(services) as client:
        body = (await client.get("/api/v1/goals", headers=AUTH)).json()
    assert body["available"] is True and body["tasks_linked"] is True
    goals = body["goals"]
    assert [g["title"] for g in goals] == ["Launch v1", "Next year", "Old idea"]
    assert [g["state"] for g in goals] == ["active", "planned", "dropped"]
    assert goals[0]["progress"] == 0.3 and goals[1]["progress"] == 1.0  # capped
    assert goals[2]["progress"] is None
    assert [t["title"] for t in goals[0]["open_tasks"]] == [
        "Fix the signup bug",
        "Write the launch post",
    ]


async def test_reading_lists_the_queue_in_progress_first(session_factory):
    fake = FakeNotion()
    books = SPECS["books"]
    fake.seed(books, {"title": "Later", "status": "Want to Read", "up_next": 2})
    fake.seed(books, {"title": "Sooner", "status": "Want to Read", "up_next": 1})
    fake.seed(books, {"title": "Current", "status": "Reading", "pages": 400, "pages_read": 100})
    fake.seed(books, {"title": "On the shelf only"})  # no status: not in the queue
    services, _ = services_for(session_factory, fake)
    async with client_for(services) as client:
        body = (await client.get("/api/v1/reading", headers=AUTH)).json()
    assert [b["title"] for b in body["books"]] == ["Current", "Sooner", "Later"]
    assert body["books"][0]["state"] == "reading" and body["books"][0]["progress"] == 0.25


async def test_unmapped_modules_are_reported_as_unavailable(session_factory):
    services, owner = services_for(session_factory, FakeNotion())
    owner.workspace.goals = None
    owner.workspace.books = None
    async with client_for(services) as client:
        goals = (await client.get("/api/v1/goals", headers=AUTH)).json()
        reading = (await client.get("/api/v1/reading", headers=AUTH)).json()
    assert goals == {"available": False, "goals": []}
    assert reading == {"available": False, "books": []}


def daily_run(user_id, workflow, started, *, day=None, status="completed", **summary):
    return AgentRun(
        id=uuid.uuid4(), user_id=user_id, workflow=workflow, trigger="test", thread_id="t",
        status=status, started_at=started,
        output={"summary": {"workflow": workflow, "day": day, **summary}} if day else {},
    )  # fmt: skip


async def test_history_pairs_each_days_briefing_with_its_check_in(session_factory):
    other = await add_user(session_factory, "other@example.com")
    yesterday = NOW - timedelta(days=1)
    async with session_factory() as db, db.begin():
        db.add_all([
            daily_run(OWNER_ID, "morning_briefing", yesterday.replace(hour=2), day="2026-11-02",
                      priorities=["Ship the API"]),
            daily_run(OWNER_ID, "evening_checkin", yesterday.replace(hour=15), day="2026-11-02",
                      done=["Ship the API"]),
            # A late check-in that started after midnight still counts for its own day.
            daily_run(OWNER_ID, "evening_checkin", NOW.replace(hour=0), day="2026-11-02",
                      done=["Ship the API", "Write the docs"]),
            # Waiting for an answer: no summary yet, so the start time gives the day.
            daily_run(OWNER_ID, "morning_briefing", NOW.replace(hour=3), status="waiting_approval"),
            daily_run(OWNER_ID, "weekly_review", NOW.replace(hour=4), day="2026-11-03"),
            daily_run(OWNER_ID, "morning_briefing", NOW - timedelta(days=30), day="2026-10-04"),
            daily_run(other.id, "morning_briefing", NOW.replace(hour=2), day="2026-11-03"),
        ])  # fmt: skip
    services, _ = services_for(session_factory, FakeNotion())
    async with client_for(services) as client:
        days = (await client.get("/api/v1/history?days=14", headers=AUTH)).json()
    assert [d["day"] for d in days] == ["2026-11-03", "2026-11-02"]
    today, before = days
    assert today["briefing"]["status"] == "waiting_approval" and today["checkin"] is None
    assert today["briefing"]["summary"] is None
    assert before["briefing"]["summary"]["priorities"] == ["Ship the API"]
    assert before["checkin"]["summary"]["done"] == ["Ship the API", "Write the docs"]
