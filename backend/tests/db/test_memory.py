"""Memory: an account's notes, managed over the API and given to planning and chat as
preferences; chat only suggests notes, which the account saves itself."""

import json
from datetime import UTC, datetime

import httpx
from sqlalchemy import func, select

from app.api.main import create_app
from app.core.config import Settings
from app.db.models import AuditLog, MemoryFact
from app.llm.mock import MockLLM
from app.services.memory import MAX_NOTES, add_note
from tests.fake_notion import FakeNotion
from tests.workflow_support import OWNER_ID, add_user, make_runner, make_services, seed_task

NOW = datetime(2026, 11, 3, 3, 0, tzinfo=UTC)  # 08:30 in Asia/Kolkata
AUTH = {"Authorization": "Bearer tok"}


def client_for(session_factory, mock=None, **settings) -> httpx.AsyncClient:
    config = Settings(_env_file=None, lifeos_api_token="tok", **settings)
    services, _ = make_services(session_factory, FakeNotion(), mock, now=NOW, settings=config)
    transport = httpx.ASGITransport(app=create_app(services))
    return httpx.AsyncClient(transport=transport, base_url="https://t", headers=AUTH)


async def test_notes_are_added_edited_and_deleted(session_factory):
    async with client_for(session_factory) as client:
        added = await client.post("/api/v1/memory", json={"text": "  Deep work\n before noon "})
        assert added.status_code == 201
        note = added.json()
        assert note["text"] == "Deep work before noon" and note["source"] == "you"
        edited = await client.put(f"/api/v1/memory/{note['id']}", json={"text": "Deep work first"})
        assert edited.json()["text"] == "Deep work first"
        listing = (await client.get("/api/v1/memory")).json()
        assert [n["text"] for n in listing["notes"]] == ["Deep work first"]
        assert listing["limits"] == {"notes": 30, "chars": 200} and listing["editable"] is True
        assert (await client.delete(f"/api/v1/memory/{note['id']}")).status_code == 204
        missing = await client.delete(f"/api/v1/memory/{note['id']}")
        assert missing.status_code == 404 and missing.json()["error"]["code"] == "unknown_note"
        assert (await client.get("/api/v1/memory")).json()["notes"] == []
    async with session_factory() as db:
        entries = list(await db.scalars(select(AuditLog).where(AuditLog.event.like("memory.%"))))
    assert [e.event for e in entries] == ["memory.add", "memory.edit", "memory.delete"]
    # The audit log says that a note changed, never what it said.
    assert all("Deep work" not in json.dumps(e.details) for e in entries)


async def test_notes_are_checked_and_limited(session_factory):
    async with client_for(session_factory) as client:
        for text in ("   ", "x" * 201):
            response = await client.post("/api/v1/memory", json={"text": text})
            assert response.status_code == 422, text
        for i in range(MAX_NOTES):
            await add_note(session_factory, OWNER_ID, f"Note {i}")
        full = await client.post("/api/v1/memory", json={"text": "One more"})
        assert full.status_code == 409 and full.json()["error"]["code"] == "memory_full"


async def test_an_account_only_sees_and_changes_its_own_notes(session_factory):
    other = await add_user(session_factory, "other@example.com")
    theirs = await add_note(session_factory, other.id, "Their note")
    async with client_for(session_factory) as client:
        assert (await client.get("/api/v1/memory")).json()["notes"] == []
        edit = await client.put(f"/api/v1/memory/{theirs.id}", json={"text": "Mine now"})
        assert edit.status_code == 404
        assert (await client.delete(f"/api/v1/memory/{theirs.id}")).status_code == 404
    async with session_factory() as db:
        assert (await db.get(MemoryFact, theirs.id)).text == "Their note"


async def test_the_shared_demo_memory_is_read_only(session_factory):
    async with client_for(session_factory, lifeos_demo_mode=True) as client:
        assert (await client.get("/api/v1/memory")).json()["editable"] is False
        refused = await client.post("/api/v1/memory", json={"text": "Visible to everyone"})
        assert refused.status_code == 403 and refused.json()["error"]["code"] == "demo_account"


async def test_planning_sees_the_notes_as_preferences(session_factory):
    await add_note(session_factory, OWNER_ID, "Mornings are for <b>deep work</b>")
    fake = FakeNotion()
    seed_task(fake, "Draft the design doc", priority="P1")
    mock = MockLLM()
    mock.queue("DayPlan", {"picks": [{"ref": "T1", "reason": "Deep work first."}]})
    runner, _ = make_runner(session_factory, fake, mock, now=NOW)
    await runner.start("morning_briefing")
    prompt = mock.calls[0]["prompt"]
    assert "<owner_memory>\n- Mornings are for b deep work /b\n</owner_memory>" in prompt
    assert prompt.index("<owner_memory>") < prompt.index("<notion_data>")


def sse(text: str) -> list[dict]:
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


async def test_chat_suggests_a_note_and_never_saves_it(session_factory):
    await add_note(session_factory, OWNER_ID, "Fridays are light")
    mock = MockLLM()
    mock.queue(
        "ChatStep",
        {"action": "tool", "tool": "suggest_memory", "note": "I do deep work before noon"},
        {"action": "tool", "tool": "suggest_memory", "note": "Fridays are light"},
        {"action": "answer", "answer": "I suggested a note; save it if it's right."},
    )
    async with client_for(session_factory, mock) as client:
        response = await client.post(
            "/api/v1/chat", json={"message": "Remember that I do deep work before noon"}
        )
    events = sse(response.text)
    [suggestion] = [e for e in events if e["type"] == "memory_suggestion"]
    assert suggestion["text"] == "I do deep work before noon"
    [refused] = [e for e in events if e["type"] == "tool_refused"]
    assert refused["reason"] == "that note is already saved"
    assert "- Fridays are light" in mock.calls[0]["prompt"]
    async with session_factory() as db:
        assert await db.scalar(select(func.count()).select_from(MemoryFact)) == 1
