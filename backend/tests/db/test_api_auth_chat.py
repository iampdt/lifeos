"""Owner sessions, CSRF, approvals, chat (SSE), settings and Notion status over HTTP."""

import json
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from app.api.main import create_app
from app.core.config import Settings
from app.db.models import ActionProposal, AgentRun
from app.llm.base import LLMUnavailable
from app.llm.mock import MockLLM
from tests.factories import data_source_schema
from tests.fake_notion import FakeNotion
from tests.workflow_support import SPECS, make_services, read, seed_task, set_password

NOW = datetime(2026, 11, 1, 15, 0, tzinfo=UTC)
EMAIL, PASSWORD = "owner@example.com", "a long test password"


class Env:
    def __init__(self, client, app, fake, mock, session_factory):
        self.client, self.app, self.fake, self.mock = client, app, fake, mock
        self.session_factory = session_factory

    async def login(self) -> str:
        response = await self.client.post(
            "/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD}
        )
        assert response.status_code == 200, response.text
        return response.json()["csrf_token"]


@pytest.fixture
async def env(session_factory):
    fake = FakeNotion()
    mock = MockLLM()
    settings = Settings(_env_file=None, lifeos_api_token="test-token")
    services, _ = make_services(session_factory, fake, mock, now=NOW, settings=settings)
    app = create_app(services)
    await set_password(session_factory, PASSWORD)
    transport = httpx.ASGITransport(app=app)
    # https, because the session cookie is Secure.
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
        yield Env(client, app, fake, mock, session_factory)


async def test_login_sets_a_hardened_cookie_and_me_works(env):
    bad = await env.client.post("/api/v1/auth/login", json={"email": EMAIL, "password": "nope"})
    assert bad.status_code == 401 and bad.json()["error"]["code"] == "invalid_credentials"
    response = await env.client.post(
        "/api/v1/auth/login", json={"email": " OWNER@example.com", "password": PASSWORD}
    )
    cookie = response.headers["set-cookie"].lower()
    assert "lifeos_session=" in cookie and "httponly" in cookie
    assert "secure" in cookie and "samesite=lax" in cookie
    me = (await env.client.get("/api/v1/auth/me")).json()
    assert me["email"] == EMAIL and me["csrf_token"] == response.json()["csrf_token"]


async def test_mutations_need_the_csrf_header(env):
    csrf = await env.login()
    url = "/api/v1/workflows/morning_briefing/runs"
    denied = await env.client.post(url, json={})
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "csrf_failed"
    wrong = await env.client.post(url, json={}, headers={"X-CSRF-Token": "forged"})
    assert wrong.status_code == 403
    ok = await env.client.post(url, json={}, headers={"X-CSRF-Token": csrf})
    assert ok.status_code == 201
    assert (await env.client.get("/api/v1/tasks")).status_code == 200  # reads need no header


async def test_logout_revokes_the_session(env):
    csrf = await env.login()
    token = env.client.cookies.get("lifeos_session")
    out = await env.client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
    assert out.status_code == 200 and "lifeos_session" not in env.client.cookies
    # Replay the old cookie explicitly: the server must refuse it, not just the browser forget it.
    replay = await env.client.get("/api/v1/auth/me", headers={"Cookie": f"lifeos_session={token}"})
    assert replay.status_code == 401


async def test_expired_session_and_login_rate_limit(env):
    await env.login()
    env.app.state.services.clock = lambda: NOW + timedelta(days=15)
    assert (await env.client.get("/api/v1/auth/me")).status_code == 401
    env.app.state.services.clock = lambda: NOW
    codes = [
        (
            await env.client.post("/api/v1/auth/login", json={"email": EMAIL, "password": "x"})
        ).status_code
        for _ in range(5)
    ]
    assert codes == [401, 401, 401, 401, 429]  # the first login above used one attempt
    assert (await env.client.get("/api/v1/tasks", headers={"Authorization": "Bearer test-token"})
            ).status_code == 200  # fmt: skip


def sse(text: str) -> list[dict]:
    return [
        json.loads(line[len("data: ") :])
        for block in text.strip().split("\n\n")
        for line in block.splitlines()
        if line.startswith("data: ")
    ]


async def test_chat_reads_proposes_and_owner_approves(env):
    task_id = seed_task(env.fake, "Write tests", priority="P1", due=date(2026, 11, 1))
    env.mock.queue(
        "ChatStep",
        {"action": "tool", "tool": "get_today"},
        {"action": "tool", "tool": "propose_mark_done", "ref": "T1"},
        {"action": "answer", "answer": "I proposed marking T1 done; approve it in Approvals."},
    )
    csrf = await env.login()
    response = await env.client.post(
        "/api/v1/chat", json={"message": "I finished writing tests"},
        headers={"X-CSRF-Token": csrf},
    )  # fmt: skip
    assert response.headers["content-type"].startswith("text/event-stream")
    # Without no-transform, compressing proxies (e.g. `next start`) buffer the whole stream.
    assert "no-transform" in response.headers["cache-control"]
    events = sse(response.text)
    assert [e["type"] for e in events] == ["run", "tool", "tool", "proposal", "answer", "done"]
    answer = events[4]["text"]
    assert "“Write tests”" in answer and "T1" not in answer
    card = events[3]["proposal"]
    assert card["kind"] == "task.mark_done" and card["target"]["title"] == "Write tests"
    assert "<notion_data>" in env.mock.calls[1]["prompt"]  # tool output reached the model as data

    pending = (await env.client.get("/api/v1/approvals")).json()
    assert pending["runs"] == [] and [c["id"] for c in pending["standalone"]] == [card["id"]]
    approved = await env.client.post(
        f"/api/v1/proposals/{card['id']}/approve", json={}, headers={"X-CSRF-Token": csrf}
    )
    body = approved.json()
    assert body["approved"] == [card["id"]] and body["results"][0]["outcome"] == "verified"
    assert read(env.fake, "tasks", task_id)["status"] == "Done"
    async with env.session_factory() as db:
        run = await db.get(AgentRun, events[0]["run_id"])
    assert run.workflow == "chat" and run.status == "completed" and run.output["tool_calls"] == 2


async def test_chat_enforces_r1_and_rejects_unknown_refs(env):
    day = date(2026, 11, 3)
    for title in ("A", "B", "C"):
        seed_task(env.fake, title, due=day)
    seed_task(env.fake, "Move me", due=date(2026, 11, 1))
    env.mock.queue(
        "ChatStep",
        {"action": "tool", "tool": "propose_mark_done", "ref": "T7"},
        {"action": "tool", "tool": "get_today"},
        {"action": "tool", "tool": "propose_reschedule", "ref": "T1", "date": "2026-11-03"},
        {"action": "answer", "answer": "Tuesday is full."},
    )
    csrf = await env.login()
    events = sse((await env.client.post("/api/v1/chat", json={"message": "move it to tuesday"},
                                        headers={"X-CSRF-Token": csrf})).text)  # fmt: skip
    refused = [e for e in events if e["type"] == "tool_refused"]
    assert len(refused) == 2
    assert "unknown task reference" in refused[0]["reason"]
    assert "already has 3 major tasks (R1)" in refused[1]["reason"]
    async with env.session_factory() as db:
        assert list(await db.scalars(select(ActionProposal))) == []


async def test_chat_reports_when_the_model_is_down(env):
    env.mock.queue("ChatStep", LLMUnavailable("down"))
    csrf = await env.login()
    events = sse((await env.client.post("/api/v1/chat", json={"message": "hi"},
                                        headers={"X-CSRF-Token": csrf})).text)  # fmt: skip
    assert [e["type"] for e in events] == ["run", "error", "done"]
    assert events[1]["code"] == "llm_unavailable"


async def test_run_owned_proposals_are_decided_through_the_run(env):
    seed_task(env.fake, "Write tests", priority="P1", due=date(2026, 11, 1))
    csrf = await env.login()
    headers = {"X-CSRF-Token": csrf}
    start = "/api/v1/workflows/evening_checkin/runs"
    body = {"inputs": {"plan_tomorrow": False}}
    run = (await env.client.post(start, json=body, headers=headers)).json()
    answer = {"answer": {"done_refs": ["T1"]}}
    resume = f"/api/v1/runs/{run['id']}/resume"
    run = (await env.client.post(resume, json=answer, headers=headers)).json()
    approvals = (await env.client.get("/api/v1/approvals")).json()
    [waiting] = approvals["runs"]
    assert waiting["run_id"] == run["id"] and approvals["standalone"] == []
    proposal_id = waiting["proposals"][0]["id"]
    conflict = await env.client.post(f"/api/v1/proposals/{proposal_id}/approve", json={},
                                     headers=headers)  # fmt: skip
    assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "run_owned"
    recent = (await env.client.get("/api/v1/runs")).json()
    assert recent[0]["id"] == run["id"] and recent[0]["status"] == "waiting_approval"


async def test_settings_and_notion_status(env):
    env.fake.schemas = {
        SPECS[name].data_source_id: data_source_schema(SPECS[name])
        for name in ("tasks", "habits", "weeks", "months", "goals")
    }
    await env.login()
    settings = (await env.client.get("/api/v1/settings")).json()
    assert settings["writes_enabled"] is True and settings["llm"]["chains"]["fast"] == ["mock-fast"]
    assert settings["modes"]["task.mark_done"] == "approve" and settings["today"] == "2026-11-01"
    status = (await env.client.get("/api/v1/notion/status")).json()
    assert status["bot"] == "LifeOs" and status["workspace"] == "Test workspace"
    by_name = {s["name"]: s for s in status["data_sources"]}
    assert by_name["tasks"]["ok"] is True and by_name["tasks"]["drift"] == []
    assert by_name["books"]["reachable"] is False


async def test_configured_secrets_ignore_surrounding_whitespace(env):
    env.app.state.services.settings = Settings(_env_file=None, lifeos_api_token=" tok-123\n")
    ok = await env.client.get("/api/v1/tasks", headers={"Authorization": "Bearer tok-123"})
    assert ok.status_code == 200
    wrong = await env.client.get("/api/v1/tasks", headers={"Authorization": "Bearer tok-124"})
    assert wrong.status_code == 401
