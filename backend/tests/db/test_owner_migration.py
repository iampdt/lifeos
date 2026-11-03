"""The owner moves from the environment's workspace (an internal integration and the YAML
mapping) to its own Notion connection, keeping its mapping and routine times."""

from datetime import UTC, date, datetime

import httpx
import pytest
from cryptography.fernet import Fernet
from langgraph.checkpoint.memory import InMemorySaver

from app.api.cron import _schedules
from app.api.main import Services, create_app
from app.core.config import Settings
from app.core.crypto import TokenCipher
from app.demo.notion import schema_for
from app.llm.access import AccountLLM
from app.llm.mock import MockLLM
from app.notion.connections import NotionConnections
from app.notion.oauth import NotionOAuth, TokenGrant
from app.workflows.contexts import Contexts
from app.workflows.deps import WorkflowDeps
from app.workspaces.store import AccountWorkspaces
from tests.fake_notion import FakeNotion
from tests.notion_http import NotionOverHTTP
from tests.workflow_support import (
    OWNER_EMAIL,
    OWNER_ID,
    SPECS,
    make_deps,
    router,
    seed_task,
    set_password,
)

NOW = datetime(2026, 11, 3, 10, 0, tzinfo=UTC)
PASSWORD = "a long test password"


@pytest.fixture
async def owner(session_factory):
    await set_password(session_factory, PASSWORD)
    environment = FakeNotion()  # what NOTION_TOKEN reaches
    seed_task(environment, "From the environment", due=date(2026, 11, 3))
    own = FakeNotion()  # the same databases, reached through the owner's OAuth connection
    for spec in SPECS.values():
        if spec.configured:
            own.schemas[spec.data_source_id] = schema_for(spec)
    seed_task(own, "From the connection", due=date(2026, 11, 3))
    server = NotionOverHTTP(own)
    transport = httpx.MockTransport(server.handler)
    base = "https://notion.test/v1"
    oauth = NotionOAuth("id", "secret", "https://app.test/cb", base_url=base,
                        http=httpx.AsyncClient(transport=transport))  # fmt: skip
    connections = NotionConnections(session_factory, TokenCipher(Fernet.generate_key().decode()),
                                    oauth, base_url=base, transport=transport,
                                    requests_per_second=1000)  # fmt: skip
    service_llm = router(MockLLM())

    def account_deps(notion, specs, user_id, tz, rules, is_owner):
        return WorkflowDeps(session_factory, notion, specs,
                            service_llm if is_owner else AccountLLM(user_id, None),
                            writes_enabled=True, user_id=user_id, tz=tz, rules=rules,
                            clock=lambda: NOW, workspace_source="account")  # fmt: skip

    accounts = AccountWorkspaces(session_factory, connections, account_deps)
    contexts = Contexts(
        lambda uid: make_deps(session_factory, environment, None, now=NOW, user_id=uid),
        InMemorySaver(),
        account_deps=accounts.deps_for,
    )
    services = Services(Settings(_env_file=None), session_factory, contexts, clock=lambda: NOW,
                        connections=connections, environment_specs={
                            n: s for n, s in SPECS.items() if s.configured})  # fmt: skip
    transport_app = httpx.ASGITransport(app=create_app(services))
    async with httpx.AsyncClient(transport=transport_app, base_url="https://t") as client:
        login = await client.post(
            "/api/v1/auth/login", json={"email": OWNER_EMAIL, "password": PASSWORD}
        )
        client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
        yield client, services, connections, server, own


async def titles(client) -> list[str]:
    return [t["title"] for t in (await client.get("/api/v1/tasks?view=today")).json()]


async def test_the_owner_moves_its_workspace_to_its_own_connection(owner):
    client, services, connections, server, _ = owner
    assert await titles(client) == ["From the environment"]
    view = (await client.get("/api/v1/workspace")).json()
    assert view["source"] == "environment" and view["can_import"] is False  # not connected yet

    await connections.save(OWNER_ID, TokenGrant.from_response(server._grant()))
    view = (await client.get("/api/v1/workspace")).json()
    assert view["can_import"] is True

    moved = await client.post("/api/v1/workspace/import-environment")
    assert moved.status_code == 200, moved.text
    assert moved.json()["source"] == "account" and moved.json()["editable"] is True
    assert set(moved.json()["modules"]) >= {"tasks", "habits", "weeks", "months"}
    assert moved.json()["settings"]["morning_time"] == "07:30"  # the routine times came along

    assert await titles(client) == ["From the connection"]  # now through the connection
    settings = (await client.get("/api/v1/settings")).json()
    assert settings["notion_profile"] == "account"
    context = await services.contexts.for_user(OWNER_ID, is_owner=True)
    assert not isinstance(context.deps.llm, AccountLLM)  # still the deployment's own key
    [(user_id, is_owner, _, saved)] = await _schedules(services)
    assert (user_id, is_owner, saved) == (OWNER_ID, True, True)


async def test_moving_needs_every_database_shared_with_the_connection(owner):
    client, _, connections, server, own = owner
    await connections.save(OWNER_ID, TokenGrant.from_response(server._grant()))
    del own.schemas[SPECS["habits"].data_source_id]  # a page left out on Notion's consent screen
    refused = await client.post("/api/v1/workspace/import-environment")
    assert refused.status_code == 422
    [problem] = refused.json()["error"]["details"]
    assert problem["module"] == "habits" and "can't see" in problem["problem"]
    assert await titles(client) == ["From the environment"]  # nothing changed


async def test_only_the_owner_can_import_and_only_with_an_environment(owner, session_factory):
    client, services, _, _, _ = owner
    services.environment_specs = None
    assert (await client.post("/api/v1/workspace/import-environment")).status_code == 404
