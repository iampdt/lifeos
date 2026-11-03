"""Permission modes per action kind: off is never proposed, suggest is shown but never
executed, approve (the default) runs after approval."""

import uuid
from datetime import UTC, date, datetime

import httpx
import pytest

from app.api.main import create_app
from app.core.config import Settings
from app.llm.mock import MockLLM
from tests.fake_notion import FakeNotion
from tests.workflow_support import make_services, seed_task

NOW = datetime(2026, 11, 1, 15, 0, tzinfo=UTC)  # Sunday evening in Asia/Kolkata
AUTH = {"Authorization": "Bearer tok"}


@pytest.fixture
async def api(session_factory):
    fake = FakeNotion()
    seed_task(fake, "Write tests", priority="P1", due=date(2026, 11, 1))
    settings = Settings(_env_file=None, lifeos_api_token="tok")
    services, _ = make_services(session_factory, fake, MockLLM(), now=NOW, settings=settings)
    transport = httpx.ASGITransport(app=create_app(services))
    async with httpx.AsyncClient(transport=transport, base_url="https://t", headers=AUTH) as c:
        yield c


async def checkin(client, answer) -> dict:
    run = (await client.post("/api/v1/workflows/evening_checkin/runs",
                             json={"inputs": {"plan_tomorrow": False}})).json()  # fmt: skip
    return (await client.post(f"/api/v1/runs/{run['id']}/resume", json={"answer": answer})).json()


async def test_modes_are_listed_and_changed(api):
    modes = (await api.get("/api/v1/settings")).json()["modes"]
    assert set(modes.values()) == {"approve"}
    changed = await api.put("/api/v1/settings/modes", json={"modes": {"habit.upsert_day": "off"}})
    assert changed.json()["modes"]["habit.upsert_day"] == "off"
    assert (await api.get("/api/v1/settings")).json()["modes"]["habit.upsert_day"] == "off"
    for bad in ({"habit.upsert_day": "auto"}, {"task.delete": "off"}, {"task.create": "maybe"}):
        assert (await api.put("/api/v1/settings/modes", json={"modes": bad})).status_code == 422


async def test_a_kind_switched_off_is_never_proposed_and_the_run_says_so(api):
    await api.put("/api/v1/settings/modes", json={"modes": {"habit.upsert_day": "off"}})
    run = await checkin(api, {"done_refs": ["T1"], "habits": {"dsa": True}})
    kinds = [c["kind"] for c in run["waiting_for"]["proposals"]]
    assert kinds == ["task.mark_done"]
    done = (await api.post(f"/api/v1/runs/{run['id']}/resume",
                           json={"answer": {"approve": "all"}})).json()  # fmt: skip
    assert (
        "Scorecard changes are off in Settings, so none were proposed."
        in done["summary"]["warnings"]
    )


async def test_a_suggestion_is_shown_but_never_executed(api):
    await api.put("/api/v1/settings/modes", json={"modes": {"task.mark_done": "suggest"}})
    run = await checkin(api, {"done_refs": ["T1"]})
    [card] = run["waiting_for"]["proposals"]
    assert card["kind"] == "task.mark_done"
    done = (await api.post(f"/api/v1/runs/{run['id']}/resume",
                           json={"answer": {"approve": "all"}})).json()  # fmt: skip
    assert done["summary"]["proposals"]["approved"] == 0
    assert "suggest mode" in next(iter(done["summary"]["proposals"]["errors"].values()))
    assert done["summary"]["execution"] == {}
    assert uuid.UUID(done["id"])
