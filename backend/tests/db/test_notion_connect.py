"""Connecting an account's own Notion: the OAuth flow, encrypted tokens, refresh with rotation,
reconnection, disconnecting and account deletion, against a fake Notion."""

import asyncio
import base64
import json
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import func, select

from app.api.main import create_app
from app.core.config import Settings
from app.core.crypto import TokenCipher
from app.db.models import (
    AuditLog,
    KnowledgeChunk,
    KnowledgePage,
    NotionConnection,
    OAuthState,
)
from app.llm.mock import MockLLM
from app.notion.connections import NotionConnections
from app.notion.oauth import NotionOAuth
from tests.fake_notion import FakeNotion
from tests.workflow_support import OWNER_EMAIL, OWNER_ID, add_user, make_services, set_password

NOW = datetime(2026, 11, 1, 15, 0, tzinfo=UTC)
BASE = "https://notion.test/v1"
REDIRECT = "https://app.test/api/v1/notion/callback"
PASSWORD = "a long test password"
KEY = Fernet.generate_key().decode()


class FakeNotionAuth:
    """Notion's token, revoke, search and users/me endpoints, with rotating refresh tokens."""

    def __init__(self) -> None:
        self.issued = 0
        self.valid_access: set[str] = set()
        self.valid_refresh: set[str] = set()
        self.revoked: list[str] = []
        self.refreshes = 0
        self.refresh_status = 200  # set to 500 to simulate an outage
        self.expires_in: int | None = None

    def _grant(self) -> dict:
        self.issued += 1
        access, refresh = f"access-{self.issued}", f"refresh-{self.issued}"
        self.valid_access.add(access)
        self.valid_refresh.add(refresh)
        body = {
            "access_token": access, "token_type": "bearer", "refresh_token": refresh,
            "bot_id": "bot-1", "workspace_id": "ws-1", "workspace_name": "Acme",
            "workspace_icon": "🚀", "duplicated_template_id": None,
            "owner": {"type": "user", "user": {"object": "user", "id": "u-1"}},
        }  # fmt: skip
        if self.expires_in:
            body["expires_in"] = self.expires_in
        return body

    def expire_access(self) -> None:
        self.valid_access.clear()

    async def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/v1/")
        if path.startswith("oauth/"):
            expected = base64.b64encode(b"notion-client:notion-secret").decode()
            assert request.headers["authorization"] == f"Basic {expected}"
            body = json.loads(request.content)
            if path == "oauth/revoke":
                self.revoked.append(body["token"])
                self.valid_access.discard(body["token"])
                return httpx.Response(200, json={"request_id": "r"})
            if body["grant_type"] == "authorization_code":
                if body["code"] != "the-code" or body["redirect_uri"] != REDIRECT:
                    return httpx.Response(400, json={"error": "invalid_grant"})
                return httpx.Response(200, json=self._grant())
            self.refreshes += 1
            await asyncio.sleep(0.01)  # lets a concurrent request reach the lock
            if self.refresh_status != 200:
                return httpx.Response(self.refresh_status, json={"error": "server_error"})
            if body["refresh_token"] not in self.valid_refresh:
                return httpx.Response(400, json={"error": "invalid_grant"})
            self.valid_refresh.discard(body["refresh_token"])  # rotation
            return httpx.Response(200, json=self._grant())
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        if token not in self.valid_access:
            error = {"object": "error", "status": 401, "code": "unauthorized", "message": "no"}
            return httpx.Response(401, json=error)
        if path == "search":
            body = json.loads(request.content)
            assert body["filter"] == {"property": "object", "value": "data_source"}
            if body.get("start_cursor") != "page-2":
                results = [self._source("ds-1", "Tasks")]
                return httpx.Response(200, json={"results": results, "has_more": True,
                                                 "next_cursor": "page-2"})  # fmt: skip
            results = [self._source("ds-2", "Habits")]
            return httpx.Response(200, json={"results": results, "has_more": False})
        return httpx.Response(200, json={"object": "user", "type": "bot"})

    @staticmethod
    def _source(id_: str, title: str) -> dict:
        return {"object": "data_source", "id": id_, "title": [{"plain_text": title}],
                "parent": {"type": "database_id", "database_id": f"db-{id_}"},
                "url": f"https://notion.so/{id_}"}  # fmt: skip


class Env:
    def __init__(self, client, other, fake, connections, session_factory):
        self.client, self.other, self.fake = client, other, fake
        self.connections, self.session_factory = connections, session_factory
        self.csrf: dict = {}

    async def sign_in(self, client, email):
        response = await client.post(
            "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
        )
        self.csrf[client] = response.json()["csrf_token"]

    async def post(self, client, url, body=None):
        return await client.post(url, json=body or {}, headers={"X-CSRF-Token": self.csrf[client]})

    async def start(self, client=None) -> str:
        response = await (client or self.client).get("/api/v1/notion/connect")
        assert response.status_code == 302, response.text
        url = urlsplit(response.headers["location"])
        assert f"{url.scheme}://{url.netloc}{url.path}" == f"{BASE}/oauth/authorize"
        params = {k: v[0] for k, v in parse_qs(url.query).items()}
        assert params["client_id"] == "notion-client" and params["redirect_uri"] == REDIRECT
        assert params["owner"] == "user" and params["response_type"] == "code"
        return params["state"]

    async def finish(self, state, client=None, **params) -> str:
        query = {"code": "the-code", "state": state, **params}
        response = await (client or self.client).get("/api/v1/notion/callback", params=query)
        assert response.status_code == 302
        return response.headers["location"]

    async def connect(self) -> None:
        assert await self.finish(await self.start()) == "/settings?notion=connected"

    async def row(self, user_id=OWNER_ID) -> NotionConnection | None:
        async with self.session_factory() as db:
            return await db.get(NotionConnection, user_id)


def build(session_factory, fake, clock=lambda: NOW):
    transport = httpx.MockTransport(fake.handler)
    oauth = NotionOAuth("notion-client", "notion-secret", REDIRECT, base_url=BASE,
                        http=httpx.AsyncClient(transport=transport))  # fmt: skip
    connections = NotionConnections(
        session_factory,
        TokenCipher(KEY),
        oauth,
        base_url=BASE,
        clock=clock,
        transport=transport,
        requests_per_second=1000,
    )
    services, _ = make_services(session_factory, FakeNotion(), MockLLM(), now=NOW,
                                connections=connections)  # fmt: skip
    return create_app(services), connections


@pytest.fixture
async def env(session_factory):
    await set_password(session_factory, PASSWORD)
    await add_user(session_factory, "other@example.com", password=PASSWORD)
    fake = FakeNotionAuth()
    app, connections = build(session_factory, fake)
    transport = httpx.ASGITransport(app=app)
    async with (
        httpx.AsyncClient(transport=transport, base_url="https://t") as client,
        httpx.AsyncClient(transport=transport, base_url="https://t") as other,
    ):
        env = Env(client, other, fake, connections, session_factory)
        await env.sign_in(client, OWNER_EMAIL)
        await env.sign_in(other, "other@example.com")
        yield env


# --- the OAuth flow ------------------------------------------------------------------------


async def test_connecting_stores_encrypted_tokens_and_the_workspace(env):
    assert (await env.client.get("/api/v1/notion/connection")).json() == {
        "available": True,
        "connection": None,
    }
    await env.connect()
    row = await env.row()
    assert row.status == "active" and row.workspace_name == "Acme" and row.bot_id == "bot-1"
    assert "access-1" not in row.access_token and "refresh-1" not in row.refresh_token
    assert TokenCipher(KEY).decrypt(row.access_token) == "access-1"
    view = (await env.client.get("/api/v1/notion/connection")).json()["connection"]
    assert view["workspace_name"] == "Acme" and view["status"] == "active"
    assert view["from_template"] is False
    async with env.session_factory() as db:
        events = (await db.scalars(select(AuditLog.event))).all()
    assert "notion.connected" in events
    # Each account sees only its own connection.
    other = (await env.other.get("/api/v1/notion/connection")).json()
    assert other["connection"] is None


async def test_an_attempt_is_single_use_and_expires(env):
    state = await env.start()
    assert await env.finish(state) == "/settings?notion=connected"
    assert await env.finish(state) == "/settings?notion_error=expired"
    assert await env.finish("made-up") == "/settings?notion_error=expired"


async def test_cancelling_and_a_bad_code(env):
    assert await env.finish(await env.start(), error="access_denied") == (
        "/settings?notion_error=cancelled"
    )
    assert await env.finish(await env.start(), code="forged") == (
        "/settings?notion_error=exchange_failed"
    )
    assert await env.row() is None


async def test_a_callback_from_another_accounts_attempt_is_refused(env):
    """A forged link can't attach someone's workspace to the account that opens it."""
    state = await env.start(env.other)
    assert await env.finish(state, client=env.client) == "/settings?notion_error=wrong_account"
    async with env.session_factory() as db:
        assert (await db.scalars(select(NotionConnection))).all() == []


async def test_connect_and_its_callback_need_the_accounts_session(session_factory):
    app, _ = build(session_factory, FakeNotionAuth())
    async with session_factory() as db, db.begin():
        db.add(OAuthState(state="valid", provider="notion", user_id=OWNER_ID,
                          expires_at=NOW + timedelta(minutes=5)))  # fmt: skip
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://t") as client:
        assert (await client.get("/api/v1/notion/connect")).status_code == 401
        callback = await client.get("/api/v1/notion/callback?state=valid&code=the-code")
        assert callback.headers["location"] == "/settings?notion_error=wrong_account"


async def test_not_configured(session_factory):
    settings = Settings(_env_file=None, lifeos_api_token="tok")
    services, _ = make_services(
        session_factory, FakeNotion(), MockLLM(), now=NOW, settings=settings
    )
    transport = httpx.ASGITransport(app=create_app(services))
    auth = {"Authorization": "Bearer tok"}
    async with httpx.AsyncClient(transport=transport, base_url="https://t") as client:
        view = (await client.get("/api/v1/notion/connection", headers=auth)).json()
        assert view == {"available": False, "connection": None}
        assert (await client.get("/api/v1/notion/connect", headers=auth)).status_code == 404
        assert (await client.get("/api/v1/notion/callback")).status_code == 404


# --- using the connection ------------------------------------------------------------------


async def test_listing_data_sources_follows_pagination(env):
    not_yet = await env.client.get("/api/v1/notion/data-sources")
    assert not_yet.status_code == 409 and not_yet.json()["error"]["code"] == "notion_reconnect"
    await env.connect()
    listing = (await env.client.get("/api/v1/notion/data-sources")).json()
    assert [d["title"] for d in listing["data_sources"]] == ["Tasks", "Habits"]
    assert listing["data_sources"][0]["database_id"] == "db-ds-1"
    assert listing["truncated"] is False


async def test_a_rejected_token_is_refreshed_once_and_rotated(env):
    await env.connect()
    env.fake.expire_access()
    listing = await env.client.get("/api/v1/notion/data-sources")
    assert listing.status_code == 200 and env.fake.refreshes == 1
    row = await env.row()
    cipher = TokenCipher(KEY)
    assert cipher.decrypt(row.access_token) == "access-2"
    assert cipher.decrypt(row.refresh_token) == "refresh-2" and row.refreshed_at is not None
    await env.client.get("/api/v1/notion/data-sources")
    assert env.fake.refreshes == 1  # the new token is used as is


async def test_concurrent_requests_refresh_only_once(env):
    """Rotation invalidates the old refresh token, so a second refresh would lock the account
    out; the row lock makes the second request reuse the first one's token."""
    await env.connect()
    env.fake.expire_access()
    clients = [env.connections.client(OWNER_ID) for _ in range(3)]
    try:
        results = await asyncio.gather(*(c.get_self() for c in clients))
    finally:
        for c in clients:
            await c.aclose()
    assert all(r["type"] == "bot" for r in results) and env.fake.refreshes == 1
    assert (await env.row()).status == "active"


async def test_a_refused_refresh_asks_for_reconnection(env):
    await env.connect()
    env.fake.expire_access()
    env.fake.valid_refresh.clear()  # access removed in Notion
    response = await env.client.get("/api/v1/notion/data-sources")
    assert response.status_code == 409 and response.json()["error"]["code"] == "notion_reconnect"
    view = (await env.client.get("/api/v1/notion/connection")).json()["connection"]
    assert view["status"] == "needs_reconnect"
    await env.connect()  # connecting again restores it
    assert (await env.row()).status == "active"
    assert (await env.client.get("/api/v1/notion/data-sources")).status_code == 200


async def test_an_outage_during_refresh_leaves_the_connection_usable(env):
    await env.connect()
    env.fake.expire_access()
    env.fake.refresh_status = 500
    response = await env.client.get("/api/v1/notion/data-sources")
    assert response.status_code == 502
    assert (await env.row()).status == "active"
    env.fake.refresh_status = 200
    assert (await env.client.get("/api/v1/notion/data-sources")).status_code == 200


async def test_tokens_with_an_expiry_are_refreshed_ahead_of_time(session_factory):
    await set_password(session_factory, PASSWORD)
    fake = FakeNotionAuth()
    fake.expires_in = 3600
    now = [NOW]
    app, connections = build(session_factory, fake, clock=lambda: now[0])
    env = Env(None, None, fake, connections, session_factory)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="https://t") as client:  # fmt: skip
        env.client = client
        await env.sign_in(client, OWNER_EMAIL)
        await env.connect()
        assert (await env.row()).expires_at == NOW + timedelta(hours=1)
        now[0] = NOW + timedelta(minutes=59, seconds=30)  # inside the refresh margin
        assert (await client.get("/api/v1/notion/data-sources")).status_code == 200
        assert fake.refreshes == 1


# --- disconnecting -------------------------------------------------------------------------


async def test_disconnecting_revokes_and_forgets_the_tokens(env):
    await env.connect()
    user_id = (await env.row()).user_id
    async with env.session_factory() as db, db.begin():
        page = KnowledgePage(user_id=user_id, page_id="p" * 32, title="Notes",
                             indexed_at=datetime.now(UTC), passages=1)  # fmt: skip
        db.add(page)
        await db.flush()
        db.add(KnowledgeChunk(user_id=user_id, page_ref=page.id, position=0, text="Copied text"))
    response = await env.post(env.client, "/api/v1/notion/disconnect")
    assert response.json() == {"status": "disconnected", "revoked": True}
    assert env.fake.revoked == ["access-1"] and await env.row() is None
    async with env.session_factory() as db:  # copies of the workspace's pages go too
        assert await db.scalar(select(func.count()).select_from(KnowledgeChunk)) == 0
        assert await db.scalar(select(func.count()).select_from(KnowledgePage)) == 0
    again = await env.post(env.client, "/api/v1/notion/disconnect")
    assert again.status_code == 409 and again.json()["error"]["code"] == "not_connected"


async def test_deleting_the_account_revokes_and_removes_the_connection(env):
    state = await env.start(env.other)
    assert await env.finish(state, client=env.other) == "/settings?notion=connected"
    async with env.session_factory() as db:
        [row] = (await db.scalars(select(NotionConnection))).all()
        other_id = row.user_id
    deleted = await env.post(env.other, "/api/v1/account/delete", {"confirm": "other@example.com"})
    assert deleted.json() == {"status": "deleted"}
    assert env.fake.revoked == ["access-1"] and await env.row(other_id) is None
