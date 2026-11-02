"""API: auth, error shape, and a workflow run driven over HTTP."""

from datetime import UTC, date, datetime

import httpx
import pytest

from app.api.main import create_app
from app.core.config import Settings
from app.llm.mock import MockLLM
from tests.fake_notion import FakeNotion
from tests.workflow_support import make_services, seed_task

NOW = datetime(2026, 11, 1, 15, 0, tzinfo=UTC)
AUTH = {"Authorization": "Bearer test-token"}


@pytest.fixture
async def api(session_factory):
    fake = FakeNotion()
    seed_task(fake, "Write tests", priority="P1", due=date(2026, 11, 1))
    settings = Settings(_env_file=None, lifeos_api_token="test-token")
    services, _ = make_services(session_factory, fake, MockLLM(), now=NOW, settings=settings)
    app = create_app(services)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, app


async def test_health_is_public_and_everything_else_needs_the_token(api):
    client, _ = api
    assert (await client.get("/api/v1/health")).json() == {"status": "ok"}
    for headers in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": "test-token"}):
        response = await client.get("/api/v1/tasks", headers=headers)
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "unauthorized"
    ready = await client.get("/api/v1/health/ready", headers=AUTH)
    assert ready.json()["database"] == "ok" and ready.json()["writes_enabled"] is True


async def test_unset_token_fails_closed(api):
    client, app = api
    app.state.services.settings = Settings(_env_file=None, lifeos_api_token=None)
    response = await client.get("/api/v1/tasks", headers=AUTH)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "auth_not_configured"


async def test_run_a_checkin_over_http(api):
    client, _ = api
    started = await client.post(
        "/api/v1/workflows/evening_checkin/runs",
        json={"inputs": {"plan_tomorrow": False}},
        headers=AUTH,
    )
    assert started.status_code == 201
    run = started.json()
    assert run["status"] == "waiting_input" and run["waiting_for"]["type"] == "question"

    url = f"/api/v1/runs/{run['id']}/resume"
    run = (await client.post(url, json={"answer": {"done_refs": ["T1"]}}, headers=AUTH)).json()
    assert run["status"] == "waiting_approval"
    pending = (await client.get("/api/v1/proposals", headers=AUTH)).json()
    assert [p["kind"] for p in pending] == ["task.mark_done"]

    run = (await client.post(url, json={"answer": {"approve": "all"}}, headers=AUTH)).json()
    assert run["status"] == "completed" and run["summary"]["done"] == ["Write tests"]
    again = await client.post(url, json={"answer": {"approve": "all"}}, headers=AUTH)
    assert again.status_code == 409 and again.json()["error"]["code"] == "run_state"
    fetched = (await client.get(f"/api/v1/runs/{run['id']}", headers=AUTH)).json()
    assert fetched["status"] == "completed"
    executed = await client.get("/api/v1/proposals?status=executed", headers=AUTH)
    assert len(executed.json()) == 1
    today = (await client.get("/api/v1/tasks?view=today", headers=AUTH)).json()
    assert today == []


async def test_errors_use_one_shape(api):
    client, _ = api
    unknown = await client.post("/api/v1/workflows/nope/runs", json={}, headers=AUTH)
    assert unknown.status_code == 404 and unknown.json()["error"]["code"] == "unknown_workflow"
    bad_view = await client.get("/api/v1/tasks?view=everything", headers=AUTH)
    assert bad_view.status_code == 422 and bad_view.json()["error"]["code"] == "invalid_request"
    missing = await client.get("/api/v1/runs/00000000-0000-0000-0000-000000000000", headers=AUTH)
    assert missing.status_code == 409
