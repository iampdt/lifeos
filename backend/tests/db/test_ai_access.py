"""Which key an account's model calls use: its own key, the service's allowance (with consent),
or none; and the API to agree to the free-tier terms and add or remove a key."""

from datetime import UTC, datetime

import httpx
import pytest
from cryptography.fernet import Fernet
from pydantic import BaseModel
from sqlalchemy import select

from app.agents.common import ask_model, unavailable_warning
from app.api.main import create_app
from app.core.crypto import TokenCipher
from app.db.models import AIAccess, AIUsage
from app.llm.access import DISCLOSURE_VERSION, AccountLLM, AIAccessService
from app.llm.base import LLMKeyRejected, LLMOff, LLMUnavailable
from app.llm.mock import MockLLM
from app.llm.router import LLMRouter
from tests.fake_notion import FakeNotion
from tests.workflow_support import OWNER_ID, add_user, make_services, set_password

NOW = datetime(2026, 11, 3, 10, 0, tzinfo=UTC)
CHAINS = {"fast": ["m"], "smart": ["m"]}
GOOD_KEY = "AIza-good-key-0000000000001234"
PASSWORD = "a long test password"


class Answer(BaseModel):
    text: str


class Google:
    """Stands in for Gemini: one mock provider per key, and the key check."""

    def __init__(self) -> None:
        self.service = MockLLM()
        self.own: dict[str, MockLLM] = {}

    def router(self, key: str) -> LLMRouter:
        return LLMRouter(self.own.setdefault(key, MockLLM()), CHAINS, daily_token_budget=10**12)

    async def check(self, key: str) -> None:
        if key.startswith("bad"):
            raise LLMKeyRejected("400 INVALID_ARGUMENT")
        if key.startswith("down"):
            raise LLMUnavailable("503 UNAVAILABLE")


def access_service(session_factory, google: Google, allowance=500, cipher=True):
    return AIAccessService(
        session_factory,
        LLMRouter(google.service, CHAINS),
        allowance=allowance,
        cipher=TokenCipher(Fernet.generate_key().decode()) if cipher else None,
        own_router=google.router,
        check_key=google.check,
        clock=lambda: NOW,
    )


@pytest.fixture
async def member(session_factory):
    user = await add_user(session_factory, "member@example.com", password=PASSWORD)
    return user.id


async def ask(llm) -> str:
    return (await llm.generate(Answer, system="s", prompt="p" * 400)).value.text


# --- routing --------------------------------------------------------------------------------


async def test_without_consent_or_a_key_ai_is_off_and_says_why(session_factory, member):
    ai = access_service(session_factory, Google())
    with pytest.raises(LLMOff, match="turn it on in Settings"):
        await ask(AccountLLM(member, ai))
    call = await ask_model(AccountLLM(member, ai), Answer, agent="t", system="s", prompt="p")
    assert unavailable_warning(call.error).startswith("AI is off for your account")


async def test_with_consent_the_service_key_is_used_within_the_allowance(session_factory, member):
    google = Google()
    ai = access_service(session_factory, google, allowance=250)
    await ai.consent(member)
    google.service.queue(Answer, {"text": "from the service"}, {"text": "again"})
    llm = AccountLLM(member, ai)
    assert await ask(llm) == "from the service"  # 120 tokens counted
    assert await ask(llm) == "again"  # 240 of 250
    async with session_factory() as db:
        usage = await db.get(AIUsage, (member, NOW.date()))
    assert usage.service_tokens == 240 and usage.own_tokens == 0
    google.service.queue(Answer, {"text": "never sent"})
    await ask(llm)  # still under the allowance before this call
    with pytest.raises(LLMOff, match="today's AI allowance"):
        await ask(llm)
    assert (await ai.status(member)).mode == "allowance_used"


async def test_an_own_key_comes_first_and_doesnt_touch_the_allowance(session_factory, member):
    google = Google()
    ai = access_service(session_factory, google, allowance=0)
    assert await ai.set_key(member, GOOD_KEY) == "1234"
    google.own.setdefault(GOOD_KEY, MockLLM()).queue(Answer, {"text": "from your key"})
    assert await ask(AccountLLM(member, ai)) == "from your key"
    assert google.service.calls == []
    status = await ai.status(member)
    assert status.mode == "own_key" and status.own_tokens_today == 120 and status.used_today == 0


async def test_a_refused_own_key_is_marked_and_the_allowance_takes_over(session_factory, member):
    google = Google()
    ai = access_service(session_factory, google)
    await ai.set_key(member, GOOD_KEY)
    google.own.setdefault(GOOD_KEY, MockLLM()).queue(Answer, LLMKeyRejected("400"))
    with pytest.raises(LLMOff):  # no consent, so nothing else to fall back to
        await ask(AccountLLM(member, ai))
    assert (await ai.status(member)).key_status == "rejected"
    await ai.consent(member)
    google.service.queue(Answer, {"text": "allowance"})
    assert await ask(AccountLLM(member, ai)) == "allowance"


async def test_an_own_key_out_of_quota_falls_back_to_the_allowance(session_factory, member):
    google = Google()
    ai = access_service(session_factory, google)
    await ai.set_key(member, GOOD_KEY)
    await ai.consent(member)
    google.own.setdefault(GOOD_KEY, MockLLM()).queue(Answer, LLMUnavailable("429"))
    google.service.queue(Answer, {"text": "allowance"})
    assert await ask(AccountLLM(member, ai)) == "allowance"
    assert (await ai.status(member)).key_status == "active"  # quota isn't the key's fault


async def test_keys_are_stored_encrypted_and_checked_first(session_factory, member):
    ai = access_service(session_factory, Google())
    with pytest.raises(LLMKeyRejected):
        await ai.set_key(member, "bad-key-000000000000000")
    await ai.set_key(member, GOOD_KEY)
    async with session_factory() as db:
        row = await db.get(AIAccess, member)
    assert GOOD_KEY not in row.key and ai.cipher.decrypt(row.key) == GOOD_KEY
    await ai.remove_key(member)
    async with session_factory() as db:
        row = await db.get(AIAccess, member)
    assert row.key is None and row.key_hint is None


# --- API ------------------------------------------------------------------------------------


@pytest.fixture
async def api(session_factory, member):
    await set_password(session_factory, PASSWORD)
    google = Google()
    ai = access_service(session_factory, google)
    services, _ = make_services(session_factory, FakeNotion(), MockLLM(), now=NOW, ai=ai)
    transport = httpx.ASGITransport(app=create_app(services))
    async with (
        httpx.AsyncClient(transport=transport, base_url="https://t") as client,
        httpx.AsyncClient(transport=transport, base_url="https://t") as owner,
    ):
        for c, email in ((client, "member@example.com"), (owner, "owner@example.com")):
            login = await c.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
            c.headers["X-CSRF-Token"] = login.json()["csrf_token"]
        yield client, owner, google


async def test_consent_and_keys_over_the_api(api, session_factory, member):
    client, owner, _ = api
    view = (await client.get("/api/v1/ai")).json()
    assert view["mode"] == "off" and view["consent"]["version"] == DISCLOSURE_VERSION
    assert "human reviewers may read it" in view["consent"]["text"]

    stale = await client.post("/api/v1/ai/consent", json={"version": "2020-01"})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "disclosure_changed"
    on = await client.post("/api/v1/ai/consent", json={"version": DISCLOSURE_VERSION})
    assert on.json()["mode"] == "allowance" and on.json()["allowance"]["tokens"] == 500

    bad = await client.put("/api/v1/ai/key", json={"key": "bad-key-000000000000000000"})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "key_rejected"
    down = await client.put("/api/v1/ai/key", json={"key": "down-key-00000000000000000"})
    assert down.status_code == 503 and down.json()["error"]["retryable"] is True
    added = await client.put("/api/v1/ai/key", json={"key": f"  {GOOD_KEY} "})
    assert added.json()["mode"] == "own_key" and added.json()["own_key"]["hint"] == "1234"
    assert GOOD_KEY not in added.text

    removed = await client.delete("/api/v1/ai/key")
    assert removed.json()["mode"] == "allowance" and removed.json()["own_key"]["hint"] is None
    off = await client.post("/api/v1/ai/consent/withdraw")
    assert off.json()["mode"] == "off"

    assert (await owner.get("/api/v1/ai")).json() == {"mode": "deployment"}
    refused = await owner.post("/api/v1/ai/consent", json={"version": DISCLOSURE_VERSION})
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "deployment_key"

    await client.put("/api/v1/ai/key", json={"key": GOOD_KEY})
    deleted = await client.post("/api/v1/account/delete", json={"confirm": "member@example.com"})
    assert deleted.json() == {"status": "deleted"}
    async with session_factory() as db:
        assert (await db.scalars(select(AIAccess))).all() == []
    assert OWNER_ID  # the owner's account is untouched
