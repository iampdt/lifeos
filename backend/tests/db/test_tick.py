"""The hourly tick: each account's due routines in its own timezone, once per local day, oldest
first, up to a cap; failures free their slot."""

import time
from datetime import UTC, date, datetime

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import select, update

from app.api.cron import sign
from app.api.main import Services, create_app
from app.core.config import Settings
from app.db.models import CronRun, NotionConnection, Workspace
from app.notion.errors import NotionUnavailable
from app.workflows.contexts import Contexts
from tests.fake_notion import FakeNotion
from tests.workflow_support import OWNER_ID, add_user, make_deps, seed_task

# Sunday 8 Nov, 12:45 UTC: 18:15 in Asia/Kolkata (the owner's weekly review is due) and
# 07:45 in New York (the account's morning briefing is due).
NOW = datetime(2026, 11, 8, 12, 45, tzinfo=UTC)
SECRET = "tick-secret"


def signed(job: str = "tick") -> dict[str, str]:
    ts = str(int(time.time()))
    return {"X-LifeOS-Timestamp": ts, "X-LifeOS-Signature": sign(SECRET, ts, job, b"")}


@pytest.fixture
async def tick(session_factory):
    member = await add_user(session_factory, "member@example.com")
    async with session_factory() as db, db.begin():
        db.add(Workspace(user_id=member.id, mapping={}, timezone="America/New_York",
                         max_major_per_day=3, review_weekday=0, review_time="18:00",
                         morning_time="07:30", evening_time="21:00", schedule_enabled=True,
                         paused_modules=[]))  # fmt: skip
        db.add(NotionConnection(user_id=member.id, workspace_id="ws", bot_id="b",
                                access_token="x", status="active"))  # fmt: skip
    fakes: dict = {}

    def deps_for(user_id):
        fake = fakes.setdefault(user_id, FakeNotion())
        seed_task(fake, "Write tests", priority="P1", due=date(2026, 11, 8))
        return make_deps(session_factory, fake, None, now=NOW, user_id=user_id)

    async def account_deps(user_id):
        return deps_for(user_id)

    contexts = Contexts(deps_for, InMemorySaver(), account_deps=account_deps)
    settings = Settings(_env_file=None, lifeos_cron_secret=SECRET)
    services = Services(settings, session_factory, contexts, clock=lambda: NOW)
    transport = httpx.ASGITransport(app=create_app(services))
    async with httpx.AsyncClient(transport=transport, base_url="https://t") as client:
        yield client, services, member.id


async def run(client) -> dict:
    response = await client.post("/api/v1/internal/cron/tick", headers=signed())
    assert response.status_code == 200, response.text
    return response.json()


async def test_each_account_gets_its_own_due_routine_once(tick, session_factory):
    client, _, member = tick
    first = await run(client)
    ran = {(r["user_id"], r["job"]) for r in first["ran"]}
    assert ran == {(str(OWNER_ID), "weekly_review"), (str(member), "morning_briefing")}
    assert all(r["status"] != "failed" for r in first["ran"]) and first["waiting"] == 0
    again = await run(client)
    assert again["ran"] == [] and again["waiting"] == 0
    async with session_factory() as db:
        rows = {(r.user_id, r.job, r.local_date) for r in await db.scalars(select(CronRun))}
    assert rows == {(OWNER_ID, "weekly_review", date(2026, 11, 8)),
                    (member, "morning_briefing", date(2026, 11, 8))}  # fmt: skip


async def test_the_cap_leaves_the_rest_for_the_next_tick(tick):
    client, services, _ = tick
    services.settings = Settings(_env_file=None, lifeos_cron_secret=SECRET, lifeos_tick_max_runs=1)
    first = await run(client)
    assert len(first["ran"]) == 1 and first["waiting"] == 1
    second = await run(client)
    assert len(second["ran"]) == 1 and second["waiting"] == 0
    assert first["ran"][0]["job"] != second["ran"][0]["job"]


async def test_a_failed_run_is_retried_by_a_later_tick(tick, monkeypatch, session_factory):
    client, services, _ = tick
    owner = await services.contexts.for_user(OWNER_ID, is_owner=True)

    async def down():
        raise NotionUnavailable("Notion is down")

    monkeypatch.setattr(owner.deps.workspace.tasks, "active", down)
    first = await run(client)
    failed = [r for r in first["ran"] if r["user_id"] == str(OWNER_ID)]
    assert failed and failed[0]["status"] == "failed"
    async with session_factory() as db:
        jobs = {(r.user_id, r.job) for r in await db.scalars(select(CronRun))}
    assert (OWNER_ID, "weekly_review") not in jobs
    monkeypatch.undo()
    retried = await run(client)
    assert [(r["user_id"], r["status"]) for r in retried["ran"]] == [
        (str(OWNER_ID), "waiting_input")
    ]


async def test_accounts_can_turn_their_routines_off(tick, session_factory):
    client, _, member = tick
    async with session_factory() as db, db.begin():
        await db.execute(update(Workspace).values(schedule_enabled=False))
    ran = await run(client)
    assert [r["user_id"] for r in ran["ran"]] == [str(OWNER_ID)]


async def test_a_lost_connection_stops_scheduling(tick, session_factory):
    client, _, _ = tick
    async with session_factory() as db, db.begin():
        await db.execute(update(NotionConnection).values(status="needs_reconnect"))
    ran = await run(client)
    assert [r["user_id"] for r in ran["ran"]] == [str(OWNER_ID)]
