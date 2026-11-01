"""Signed cron calls: authentication, one run per job per day, failure handling."""

import time
from datetime import UTC, date, datetime

import httpx
import pytest
from sqlalchemy import select

from app.api.cron import sign
from app.api.main import Services, create_app
from app.core.config import Settings
from app.db.models import CronRun
from app.llm.mock import MockLLM
from app.notion.errors import NotionUnavailable
from tests.fake_notion import FakeNotion
from tests.workflow_support import make_runner, seed_task

NOW = datetime(2026, 11, 2, 2, 0, tzinfo=UTC)  # 07:30 in Asia/Kolkata
SECRET = "cron-test-secret"


@pytest.fixture
async def cron(session_factory):
    fake = FakeNotion()
    seed_task(fake, "Write tests", priority="P1", due=date(2026, 11, 2))
    runner, deps = make_runner(session_factory, fake, MockLLM(), now=NOW)
    settings = Settings(_env_file=None, lifeos_cron_secret=SECRET)
    app = create_app(Services(settings, deps, runner))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://t") as c:
        yield c, app, session_factory


def signed(job: str, *, secret: str = SECRET, timestamp: int | None = None, sign_job=None):
    ts = str(timestamp if timestamp is not None else int(time.time()))
    return {
        "X-LifeOS-Timestamp": ts,
        "X-LifeOS-Signature": sign(secret, ts, sign_job or job, b""),
    }


async def call(client, job, headers):
    return await client.post(f"/api/v1/internal/cron/{job}", headers=headers)


async def test_signature_is_required(cron):
    client, _, _ = cron
    assert (await call(client, "morning_briefing", {})).status_code == 401
    wrong_secret = signed("morning_briefing", secret="nope")
    assert (await call(client, "morning_briefing", wrong_secret)).status_code == 401
    stale = signed("morning_briefing", timestamp=int(time.time()) - 600)
    assert (await call(client, "morning_briefing", stale)).status_code == 401
    other_job = signed("morning_briefing", sign_job="weekly_review")
    response = await call(client, "morning_briefing", other_job)
    assert response.status_code == 401 and response.json()["error"]["code"] == "bad_signature"


async def test_runs_once_per_local_day(cron):
    client, _, session_factory = cron
    first = await call(client, "morning_briefing", signed("morning_briefing"))
    assert first.status_code == 200 and first.json()["status"] == "completed"
    assert first.json()["date"] == "2026-11-02"
    again = await call(client, "morning_briefing", signed("morning_briefing"))
    assert again.json()["status"] == "already_ran"
    async with session_factory() as db:
        [row] = list(await db.scalars(select(CronRun)))
    assert row.job == "morning_briefing" and str(row.run_id) == first.json()["run_id"]


async def test_expire_job_and_unknown_job(cron):
    client, _, _ = cron
    expired = await call(client, "expire_proposals", signed("expire_proposals"))
    assert expired.json() == {"job": "expire_proposals", "date": "2026-11-02",
                              "status": "done", "expired": 0}  # fmt: skip
    unknown = await call(client, "rm_rf", signed("rm_rf"))
    assert unknown.status_code == 404


async def test_a_failed_run_can_be_retried_the_same_day(cron, monkeypatch):
    client, app, session_factory = cron
    tasks = app.state.services.deps.workspace.tasks

    async def down():
        raise NotionUnavailable("Notion is down")

    monkeypatch.setattr(tasks, "active", down)
    failed = await call(client, "morning_briefing", signed("morning_briefing"))
    assert failed.status_code == 502 and failed.json()["error"]["retryable"] is True
    async with session_factory() as db:
        assert list(await db.scalars(select(CronRun))) == []
    monkeypatch.undo()
    retried = await call(client, "morning_briefing", signed("morning_briefing"))
    assert retried.json()["status"] == "completed"


async def test_unset_secret_disables_cron(session_factory):
    runner, deps = make_runner(session_factory, FakeNotion(), MockLLM(), now=NOW)
    app = create_app(Services(Settings(_env_file=None), deps, runner))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://t") as c:
        response = await call(c, "morning_briefing", signed("morning_briefing"))
    assert response.status_code == 503


async def test_production_hides_the_api_docs(session_factory):
    runner, deps = make_runner(session_factory, FakeNotion(), MockLLM(), now=NOW)
    for env, expected in (("production", 404), ("development", 200)):
        app = create_app(Services(Settings(_env_file=None, lifeos_env=env), deps, runner))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://t"
        ) as c:
            assert (await c.get("/openapi.json")).status_code == expected
            assert (await c.get("/docs")).status_code == expected
