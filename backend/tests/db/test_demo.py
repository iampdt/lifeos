"""The public demo over HTTP: sign-in without a password, the sample workspace, reset."""

from datetime import UTC, datetime
from functools import partial

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import delete

from app.api.main import Services, create_app
from app.core.config import BACKEND_ROOT, Settings
from app.db.models import User
from app.demo.setup import demo_notion, demo_specs, demo_user, reset_demo
from app.llm.mock import MockLLM
from app.workflows.contexts import Contexts
from app.workflows.deps import WorkflowDeps
from tests.workflow_support import router

NOW = datetime(2026, 11, 4, 15, 0, tzinfo=UTC)  # Wednesday evening in Asia/Kolkata


async def demo_app(session_factory, demo: bool = True):
    async with session_factory() as db, db.begin():
        await db.execute(delete(User))  # a demo database holds only the demo account
    user = await demo_user(session_factory)
    make_deps = partial(
        WorkflowDeps,
        session_factory,
        demo_notion(),
        demo_specs(BACKEND_ROOT / "config" / "notion_mapping.yaml"),
        router(MockLLM()),
        writes_enabled=True,
        clock=lambda: NOW,
    )
    contexts = Contexts(lambda user_id: make_deps(user_id=user_id), InMemorySaver())
    context = await contexts.for_user(user.id, is_owner=True)
    assert context is not None
    await reset_demo(context.deps)
    settings = Settings(_env_file=None, lifeos_demo_mode=demo)
    services = Services(settings, session_factory, contexts, clock=lambda: NOW)
    return create_app(services), context.deps


@pytest.fixture
async def client(session_factory):
    app, deps = await demo_app(session_factory)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="https://demo") as c:  # fmt: skip
        yield c, deps


async def test_visitors_get_a_session_without_a_password(client):
    c, _ = client
    assert (await c.get("/api/v1/meta")).json() == {
        "demo": True,
        "google": False,
        "password": False,
        "contact": None,
    }
    assert (await c.get("/api/v1/tasks")).status_code == 401
    session = await c.post("/api/v1/auth/demo")
    assert session.status_code == 200 and session.json()["email"] == "demo@example.com"
    today = (await c.get("/api/v1/tasks?view=today")).json()
    assert {"Mock system design interview", "Update resume summary"} <= {t["title"] for t in today}
    settings = (await c.get("/api/v1/settings")).json()
    assert settings["demo"] is True and settings["writes_enabled"] is True
    status = (await c.get("/api/v1/notion/status")).json()
    assert status["workspace"] == "Sample workspace (in memory)"
    assert all(s["ok"] for s in status["data_sources"])


async def test_workflows_run_on_the_sample_workspace_and_reset_restores_it(client):
    c, deps = client
    csrf = (await c.post("/api/v1/auth/demo")).json()["csrf_token"]
    headers = {"X-CSRF-Token": csrf}
    body = {"inputs": {"plan_tomorrow": False}}
    run = (await c.post("/api/v1/workflows/evening_checkin/runs", json=body,
                        headers=headers)).json()  # fmt: skip
    titles = [t["title"] for t in run["waiting_for"]["tasks"]]
    assert "Mock system design interview" in titles
    ref = next(t["ref"] for t in run["waiting_for"]["tasks"]
               if t["title"] == "Mock system design interview")  # fmt: skip
    run = (await c.post(f"/api/v1/runs/{run['id']}/resume",
                        json={"answer": {"done_refs": [ref]}}, headers=headers)).json()  # fmt: skip
    run = (await c.post(f"/api/v1/runs/{run['id']}/resume",
                        json={"answer": {"approve": "all"}}, headers=headers)).json()  # fmt: skip
    assert run["summary"]["execution"] == {"verified": 1}
    today = {t["title"] for t in (await c.get("/api/v1/tasks?view=today")).json()}
    assert "Mock system design interview" not in today

    reset = await c.post("/api/v1/demo/reset", headers=headers)
    assert reset.json() == {"status": "reset"}
    today = {t["title"] for t in (await c.get("/api/v1/tasks?view=today")).json()}
    assert "Mock system design interview" in today
    assert (await c.get("/api/v1/runs")).json() == []
    for _ in range(2):
        await c.post("/api/v1/demo/reset", headers=headers)
    limited = await c.post("/api/v1/demo/reset", headers=headers)
    assert limited.status_code == 429
    deleted = await c.post(
        "/api/v1/account/delete", json={"confirm": "demo@example.com"}, headers=headers
    )
    assert deleted.status_code == 403 and deleted.json()["error"]["code"] == "demo_account"


async def test_demo_endpoints_do_not_exist_outside_demo_mode(session_factory):
    app, _ = await demo_app(session_factory, demo=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="https://t") as c:  # fmt: skip
        assert (await c.get("/api/v1/meta")).json()["demo"] is False
        assert (await c.post("/api/v1/auth/demo")).status_code == 404
