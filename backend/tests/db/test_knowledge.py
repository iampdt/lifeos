"""The knowledge index: choosing pages, keeping them current, hybrid search, cited answers,
and what happens without AI."""

import json
from datetime import UTC, datetime

import httpx
from sqlalchemy import func, select

from app.api.main import create_app
from app.core.config import Settings
from app.db.models import KnowledgeChunk, KnowledgePage
from app.knowledge import index as index_module
from app.llm.base import LLMUnavailable
from app.llm.mock import MockLLM
from tests.fake_notion import FakeNotion
from tests.workflow_support import OWNER_ID, add_user, make_services

NOW = datetime(2026, 11, 3, 6, 0, tzinfo=UTC)
AUTH = {"Authorization": "Bearer tok"}

PREP = [
    ("heading_2", "System design"),
    ("bulleted_list_item", "Always cover capacity estimates and failure modes."),
    ("heading_2", "Coding"),
    ("paragraph", "Three graph problems on weekdays, timed to thirty minutes."),
]
ONCALL = [
    ("heading_2", "On call"),
    ("paragraph", "Stuck charges: check the outbox worker lag, then restart the worker."),
]


class Env:
    def __init__(self, session_factory, *, mock: MockLLM | None = None, **settings) -> None:
        self.fake = FakeNotion()
        self.prep = self.fake.add_document("Interview prep plan", PREP)
        self.oncall = self.fake.add_document("Payments on-call notes", ONCALL)
        self.empty = self.fake.add_document("Blank page", [])
        self.mock = mock if mock is not None else MockLLM()
        config = Settings(_env_file=None, lifeos_api_token="tok", **settings)
        self.services, self.deps = make_services(
            session_factory, self.fake, self.mock, now=NOW, settings=config
        )
        self.session_factory = session_factory

    def client(self) -> httpx.AsyncClient:
        transport = httpx.ASGITransport(app=create_app(self.services))
        return httpx.AsyncClient(transport=transport, base_url="https://t", headers=AUTH)


async def test_pages_are_found_added_listed_and_removed(session_factory):
    env = Env(session_factory)
    async with env.client() as client:
        found = (await client.get("/api/v1/knowledge/search-pages?q=prep")).json()["results"]
        assert [r["title"] for r in found] == ["Interview prep plan"]
        assert found[0]["indexed"] is False
        added = await client.post("/api/v1/knowledge/pages", json={"page_id": env.prep})
        assert added.status_code == 201
        page = added.json()
        assert page["title"] == "Interview prep plan" and page["passages"] == 2
        assert page["embedded"] is True and page["warnings"] == []
        found = (await client.get("/api/v1/knowledge/search-pages?q=prep")).json()["results"]
        assert found[0]["indexed"] is True
        listing = (await client.get("/api/v1/knowledge")).json()
        assert [p["title"] for p in listing["pages"]] == ["Interview prep plan"]
        assert listing["limits"] == {"pages": 25} and listing["editable"] is True

        empty = await client.post("/api/v1/knowledge/pages", json={"page_id": env.empty})
        assert empty.status_code == 422 and empty.json()["error"]["code"] == "empty_page"
        hidden = await client.post("/api/v1/knowledge/pages", json={"page_id": "0" * 32})
        assert hidden.status_code == 404 and hidden.json()["error"]["code"] == "not_shared"

        assert (await client.delete(f"/api/v1/knowledge/pages/{page['id']}")).status_code == 204
        assert (await client.get("/api/v1/knowledge")).json()["pages"] == []
    async with session_factory() as db:
        assert await db.scalar(select(func.count()).select_from(KnowledgeChunk)) == 0


async def test_a_page_is_rebuilt_only_when_notion_changed_it(session_factory):
    env = Env(session_factory)
    async with env.client() as client:
        page = (await client.post("/api/v1/knowledge/pages", json={"page_id": env.prep})).json()
        same = (await client.post(f"/api/v1/knowledge/pages/{page['id']}/refresh")).json()
        assert same["changed"] is False
        env.fake.set_blocks(env.prep, [("paragraph", "Now only one passage about mocks.")])
        changed = (await client.post(f"/api/v1/knowledge/pages/{page['id']}/refresh")).json()
        assert changed["changed"] is True and changed["passages"] == 1
        # Adding a page that is already indexed refreshes it instead of duplicating it.
        again = await client.post("/api/v1/knowledge/pages", json={"page_id": env.prep})
        assert again.json()["id"] == page["id"]
    async with session_factory() as db:
        texts = list(await db.scalars(select(KnowledgeChunk.text)))
    assert texts == ["Now only one passage about mocks."]


async def test_the_number_of_pages_is_limited(session_factory, monkeypatch):
    monkeypatch.setattr(index_module, "MAX_PAGES", 1)
    env = Env(session_factory)
    async with env.client() as client:
        await client.post("/api/v1/knowledge/pages", json={"page_id": env.prep})
        full = await client.post("/api/v1/knowledge/pages", json={"page_id": env.oncall})
        assert full.status_code == 409 and full.json()["error"]["code"] == "knowledge_full"


async def test_answers_cite_the_passages_they_use(session_factory):
    env = Env(session_factory)
    index = env.deps.knowledge()
    await index.add(env.prep)
    await index.add(env.oncall)
    env.mock.queue("KnowledgeAnswer", lambda prompt: {
        "found": True,
        # S9 was never shown: dropped. S1 is cited twice but numbered once.
        "answer": "Restart the outbox worker after checking its lag [S1][S9]. Do it first [S1].",
    })  # fmt: skip
    async with env.client() as client:
        question = {"question": "What do I do about stuck charges?"}
        body = (await client.post("/api/v1/knowledge/ask", json=question)).json()
    assert body["source"] == "ai" and body["search"] == "hybrid" and body["found"] is True
    assert body["answer"] == (
        "Restart the outbox worker after checking its lag [1]. Do it first [1]."
    )
    [cited] = body["citations"]
    assert cited["n"] == 1 and cited["title"] == "Payments on-call notes"
    assert cited["heading"] == "On call" and "outbox worker" in cited["excerpt"]
    prompt = next(c for c in env.mock.calls if c["schema"] == "KnowledgeAnswer")["prompt"]
    assert prompt.index("Payments on-call notes") < prompt.index("Interview prep plan")
    assert "<notion_data>" in prompt
    tasks = [c["task"] for c in env.mock.calls if c["schema"] == "embed"]
    assert tasks == ["document", "document", "query"]
    async with env.client() as client:
        report = (await client.get("/api/v1/reports/usage")).json()
    assert report["workflows"]["knowledge"]["runs"] == 1


async def test_without_ai_search_is_by_keyword_and_shows_passages(session_factory):
    mock = MockLLM()
    mock.embed_error = LLMUnavailable("quota")
    env = Env(session_factory, mock=mock)
    async with env.client() as client:
        page = (await client.post("/api/v1/knowledge/pages", json={"page_id": env.prep})).json()
        assert page["embedded"] is False
        assert page["warnings"] == ["AI unavailable; searched by keyword only."]
        mock.queue("KnowledgeAnswer", LLMUnavailable("quota"))
        body = (await client.post("/api/v1/knowledge/ask",
                                  json={"question": "graph problems"})).json()  # fmt: skip
    assert body["source"] == "search" and body["search"] == "keyword" and body["answer"] is None
    assert [c["heading"] for c in body["citations"]] == ["Coding"]
    assert "closest passages" in body["warnings"][-1]


async def test_an_account_only_searches_and_changes_its_own_pages(session_factory):
    env = Env(session_factory)
    other = await add_user(session_factory, "other@example.com")
    await env.deps.knowledge().add(env.oncall)
    theirs = index_module.KnowledgeIndex(session_factory, env.fake, env.deps.llm, other.id)
    their_page = await theirs.add(env.prep)
    hits, _ = await theirs.search("outbox worker restart")
    assert {h.page.title for h in hits} <= {"Interview prep plan"}
    async with env.client() as client:
        gone = await client.delete(f"/api/v1/knowledge/pages/{their_page.id}")
        assert gone.status_code == 404 and gone.json()["error"]["code"] == "unknown_page"
    async with session_factory() as db:
        assert await db.get(KnowledgePage, their_page.id) is not None


async def test_the_shared_demo_pages_are_read_only(session_factory):
    env = Env(session_factory, lifeos_demo_mode=True)
    async with env.client() as client:
        refused = await client.post("/api/v1/knowledge/pages", json={"page_id": env.prep})
        assert refused.status_code == 403 and refused.json()["error"]["code"] == "demo_account"


def sse(text: str) -> list[dict]:
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


async def test_chat_searches_the_index_and_names_the_page(session_factory):
    env = Env(session_factory)
    await env.deps.knowledge().add(env.oncall)
    env.mock.queue(
        "ChatStep",
        {"action": "tool", "tool": "search_knowledge", "query": "stuck charges"},
        {"action": "answer", "answer": "Per K1, restart the outbox worker."},
    )
    async with env.client() as client:
        message = {"message": "stuck charges again?"}
        events = sse((await client.post("/api/v1/chat", json=message)).text)
    # After the answer, and only the pages it names.
    assert [e["type"] for e in events][-3:] == ["answer", "sources", "done"]
    assert [s["title"] for s in events[-2]["sources"]] == ["Payments on-call notes"]
    answer = next(e for e in events if e["type"] == "answer")["text"]
    assert answer == "Per “Payments on-call notes”, restart the outbox worker."
    prompt = [c for c in env.mock.calls if c["schema"] == "ChatStep"][1]["prompt"]
    assert 'K1 | "Payments on-call notes" › On call' in prompt


async def test_owner_index_belongs_to_the_owner(session_factory):
    env = Env(session_factory)
    row = await env.deps.knowledge().add(env.prep)
    assert row.user_id == OWNER_ID
