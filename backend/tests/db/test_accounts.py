"""Accounts: each sees and acts on only its own data, and deleting one removes all of it."""

import uuid
from collections import defaultdict
from datetime import UTC, date, datetime

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import DBAPIError

from app.api.main import Services, create_app
from app.core.config import Settings
from app.db.models import (
    ActionExecution,
    ActionProposal,
    AgentRun,
    AuditLog,
    CronRun,
    KnowledgeChunk,
    KnowledgePage,
    MemoryFact,
    Session,
    User,
)
from app.execution.actions import ActionKind, MarkDone
from app.llm.mock import MockLLM
from app.workflows.contexts import Contexts
from tests.fake_notion import FakeNotion
from tests.workflow_support import (
    OWNER_EMAIL,
    OWNER_ID,
    add_user,
    make_deps,
    make_services,
    seed_task,
    set_password,
)

NOW = datetime(2026, 11, 1, 15, 0, tzinfo=UTC)  # Sunday evening in Asia/Kolkata
PASSWORD = "a long test password"
OTHER = "other@example.com"


class EveryAccount(Contexts):
    """Gives every account a workspace, as connecting Notion will (multi-user step 9.3)."""

    async def for_user(self, user_id, *, is_owner):
        return await super().for_user(user_id, is_owner=True)


class Env:
    def __init__(self, app, owner, other, other_id, fakes, contexts, session_factory):
        self.app, self.owner, self.other, self.other_id = app, owner, other, other_id
        self.fakes, self.contexts, self.session_factory = fakes, contexts, session_factory
        self.csrf: dict[httpx.AsyncClient, str] = {}

    async def sign_in(self, client: httpx.AsyncClient, email: str) -> None:
        response = await client.post(
            "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
        )
        assert response.status_code == 200, response.text
        self.csrf[client] = response.json()["csrf_token"]

    async def post(self, client: httpx.AsyncClient, url: str, body: dict | None = None):
        return await client.post(url, json=body or {}, headers={"X-CSRF-Token": self.csrf[client]})

    async def waiting_run(self, client: httpx.AsyncClient) -> dict:
        """A check-in paused at its approval step, with one proposal."""
        started = await self.post(
            client, "/api/v1/workflows/evening_checkin/runs", {"inputs": {"plan_tomorrow": False}}
        )
        run = started.json()
        answer = {"answer": {"done_refs": ["T1"]}}
        run = (await self.post(client, f"/api/v1/runs/{run['id']}/resume", answer)).json()
        assert run["status"] == "waiting_approval"
        return run

    async def deps(self, user_id):
        return (await self.contexts.for_user(user_id, is_owner=True)).deps


def two_accounts_app(session_factory):
    fakes: defaultdict = defaultdict(FakeNotion)

    def deps_for(user_id):
        return make_deps(session_factory, fakes[user_id], MockLLM(), now=NOW, user_id=user_id)

    contexts = EveryAccount(deps_for, InMemorySaver())
    services = Services(Settings(_env_file=None), session_factory, contexts, clock=lambda: NOW)
    return create_app(services), fakes, contexts


@pytest.fixture
async def env(session_factory):
    await set_password(session_factory, PASSWORD)
    other = await add_user(session_factory, OTHER, password=PASSWORD)
    app, fakes, contexts = two_accounts_app(session_factory)
    seed_task(fakes[OWNER_ID], "Owner's task", priority="P1", due=date(2026, 11, 1))
    seed_task(fakes[other.id], "Other's task", priority="P1", due=date(2026, 11, 1))
    transport = httpx.ASGITransport(app=app)
    async with (
        httpx.AsyncClient(transport=transport, base_url="https://t") as owner_client,
        httpx.AsyncClient(transport=transport, base_url="https://t") as other_client,
    ):
        env = Env(app, owner_client, other_client, other.id, fakes, contexts, session_factory)
        await env.sign_in(owner_client, OWNER_EMAIL)
        await env.sign_in(other_client, OTHER)
        yield env


async def standalone_proposal(env: Env, user_id) -> str:
    deps = await env.deps(user_id)
    async with deps.session_factory() as db, db.begin():
        proposal, _ = await deps.proposals(db).propose(
            ActionKind.TASK_MARK_DONE,
            MarkDone(page_id="page-1", completed_on=date(2026, 11, 1)),
            reason="test",
        )
    return str(proposal.id)


# --- isolation -----------------------------------------------------------------------------


async def test_runs_of_another_account_are_invisible_and_cant_be_resumed(env):
    run = await env.waiting_run(env.owner)
    other = env.other
    assert (await other.get("/api/v1/runs")).json() == []
    peek = await other.get(f"/api/v1/runs/{run['id']}")
    assert peek.status_code == 409 and peek.json()["error"]["code"] == "run_state"
    hijack = await env.post(
        other, f"/api/v1/runs/{run['id']}/resume", {"answer": {"approve": "all"}}
    )
    assert hijack.status_code == 409
    assert (await env.owner.get(f"/api/v1/runs/{run['id']}")).json()["status"] == "waiting_approval"
    nothing = {"runs": [], "standalone": [], "automatic": []}
    assert (await other.get("/api/v1/approvals")).json() == nothing
    assert len((await env.owner.get("/api/v1/approvals")).json()["runs"]) == 1


async def test_proposals_of_another_account_cant_be_listed_or_decided(env):
    mine = await standalone_proposal(env, OWNER_ID)
    other = env.other
    assert (await other.get("/api/v1/proposals")).json() == []
    for url in (f"/api/v1/proposals/{mine}/approve", f"/api/v1/proposals/{mine}/reject"):
        response = (await env.post(other, url)).json()
        assert response["approved"] == [] and response["rejected"] == []
        assert response["errors"] == {mine: "already missing"}
    batch = (await env.post(other, "/api/v1/proposals/approve-batch", {"ids": [mine]})).json()
    assert batch["approved"] == [] and batch["results"] == []
    [card] = (await env.owner.get("/api/v1/proposals")).json()
    assert card["id"] == mine and card["status"] == "pending"


async def test_a_run_owned_proposal_check_doesnt_reveal_another_accounts_runs(env):
    run = await env.waiting_run(env.owner)
    [proposal] = run["waiting_for"]["proposals"]
    response = await env.post(env.other, f"/api/v1/proposals/{proposal['id']}/approve")
    assert response.status_code == 200 and response.json()["errors"]  # not "run_owned"


async def test_tasks_and_workspaces_are_separate(env):
    owner = [t["title"] for t in (await env.owner.get("/api/v1/tasks")).json()]
    other = [t["title"] for t in (await env.other.get("/api/v1/tasks")).json()]
    assert owner == ["Owner's task"] and other == ["Other's task"]


async def test_the_same_suggestion_is_a_separate_proposal_per_account(env):
    mine = await standalone_proposal(env, OWNER_ID)
    theirs = await standalone_proposal(env, env.other_id)
    assert mine != theirs
    async with env.session_factory() as db:
        owners = dict((await db.execute(select(ActionProposal.id, ActionProposal.user_id))).all())
    assert {str(k): v for k, v in owners.items()} == {mine: OWNER_ID, theirs: env.other_id}


async def test_an_executor_only_executes_its_own_accounts_approvals(env):
    mine = uuid.UUID(await standalone_proposal(env, OWNER_ID))
    owner = await env.deps(OWNER_ID)
    async with owner.session_factory() as db, db.begin():
        await owner.proposals(db).approve(mine)
    intruder = (await env.deps(env.other_id)).executor("test")
    result = await intruder.execute_one(mine)
    assert result.outcome == "skipped" and result.detail == "proposal is missing"
    assert await intruder.execute_approved() == []


# --- accounts without a workspace and the bearer token -------------------------------------


async def test_an_account_without_a_workspace_can_sign_in_but_not_use_one(session_factory):
    await add_user(session_factory, OTHER, password=PASSWORD)
    services, _ = make_services(session_factory, FakeNotion(), MockLLM(), now=NOW)
    transport = httpx.ASGITransport(app=create_app(services))
    async with httpx.AsyncClient(transport=transport, base_url="https://t") as client:
        login = await client.post("/api/v1/auth/login", json={"email": OTHER, "password": PASSWORD})
        assert login.status_code == 200
        me = (await client.get("/api/v1/auth/me")).json()
        assert me["email"] == OTHER and me["owner"] is False and me["workspace"] is False
        for url in ("/api/v1/tasks", "/api/v1/runs", "/api/v1/approvals", "/api/v1/settings"):
            response = await client.get(url)
            assert response.status_code == 409, url
            assert response.json()["error"]["code"] == "no_workspace"


async def test_the_bearer_token_acts_as_the_owner(session_factory):
    settings = Settings(_env_file=None, lifeos_api_token="tok")
    services, _ = make_services(
        session_factory, FakeNotion(), MockLLM(), now=NOW, settings=settings
    )
    transport = httpx.ASGITransport(app=create_app(services))
    auth = {"Authorization": "Bearer tok"}
    async with httpx.AsyncClient(transport=transport, base_url="https://t") as client:
        me = (await client.get("/api/v1/auth/me", headers=auth)).json()
        assert me == {"via": "token", "email": OWNER_EMAIL, "name": None, "csrf_token": None,
                      "owner": True, "workspace": True}  # fmt: skip
        async with session_factory() as db, db.begin():
            await db.execute(delete(User).where(User.is_owner))
        response = await client.get("/api/v1/auth/me", headers=auth)
        assert response.status_code == 503 and response.json()["error"]["code"] == "no_owner"


# --- deleting an account -------------------------------------------------------------------


async def test_deleting_an_account_removes_everything_it_stored(env):
    for client, user_id in ((env.owner, OWNER_ID), (env.other, env.other_id)):
        run = await env.waiting_run(client)
        await env.post(client, f"/api/v1/runs/{run['id']}/resume", {"answer": {"approve": "all"}})
        seed_task(env.fakes[user_id], "Another task", priority="P1", due=date(2026, 11, 1))
        await env.waiting_run(client)  # a second run, still waiting
    other_runs = [r["id"] for r in (await env.other.get("/api/v1/runs")).json()]
    noted = await env.post(env.other, "/api/v1/memory", {"text": "Mornings are for deep work"})
    assert noted.status_code == 201
    async with env.session_factory() as db, db.begin():
        page = KnowledgePage(user_id=env.other_id, page_id="p" * 32, title="Notes",
                             indexed_at=datetime.now(UTC), passages=1)  # fmt: skip
        db.add(page)
        await db.flush()
        db.add(KnowledgeChunk(user_id=env.other_id, page_ref=page.id, position=0, text="Copy"))
    async with env.session_factory() as db, db.begin():
        db.add(CronRun(user_id=env.other_id, job="morning_briefing", local_date=date(2026, 11, 1)))
    checkpointer = env.contexts.checkpointer
    for run_id in other_runs:
        assert await checkpointer.aget_tuple({"configurable": {"thread_id": run_id}})

    wrong = await env.post(env.other, "/api/v1/account/delete", {"confirm": OWNER_EMAIL})
    assert wrong.status_code == 422 and wrong.json()["error"]["code"] == "confirmation_mismatch"
    deleted = await env.post(env.other, "/api/v1/account/delete", {"confirm": " Other@Example.com"})
    assert deleted.json() == {"status": "deleted"}
    assert 'lifeos_session=""' in deleted.headers["set-cookie"]
    assert (await env.other.get("/api/v1/auth/me")).status_code == 401

    async with env.session_factory() as db:
        for model in (AgentRun, ActionProposal, CronRun, Session, MemoryFact, KnowledgePage,
                      KnowledgeChunk):  # fmt: skip
            left = await db.scalar(
                select(func.count()).select_from(model).where(model.user_id == env.other_id)
            )
            assert left == 0, model.__name__
        assert await db.get(User, env.other_id) is None
        assert await db.scalar(select(func.count()).select_from(AuditLog).where(
            AuditLog.user_id == env.other_id)) == 0  # fmt: skip
        [gone] = (
            await db.scalars(select(AuditLog).where(AuditLog.event == "account.deleted"))
        ).all()
        assert gone.user_id is None and gone.entity_id == str(env.other_id)
        # The owner's data is untouched.
        assert await db.scalar(select(func.count()).select_from(ActionExecution)) == 1
        owner_entries = await db.scalar(
            select(func.count()).select_from(AuditLog).where(AuditLog.user_id == OWNER_ID)
        )
        assert owner_entries > 0
    for run_id in other_runs:
        assert await checkpointer.aget_tuple({"configurable": {"thread_id": run_id}}) is None
    assert len((await env.owner.get("/api/v1/runs")).json()) == 2


async def test_account_deletion_needs_a_browser_session(session_factory):
    settings = Settings(_env_file=None, lifeos_api_token="tok")
    services, _ = make_services(
        session_factory, FakeNotion(), MockLLM(), now=NOW, settings=settings
    )
    transport = httpx.ASGITransport(app=create_app(services))
    async with httpx.AsyncClient(transport=transport, base_url="https://t") as client:
        response = await client.post(
            "/api/v1/account/delete",
            json={"confirm": OWNER_EMAIL},
            headers={"Authorization": "Bearer tok"},
        )
    assert response.status_code == 403 and response.json()["error"]["code"] == "session_required"


async def test_the_audit_log_stays_append_only_outside_a_purge(env):
    await standalone_proposal(env, OWNER_ID)
    await standalone_proposal(env, env.other_id)
    async with env.session_factory() as db:
        with pytest.raises(DBAPIError, match="append-only"):
            async with db.begin():
                await db.execute(text("DELETE FROM audit_log WHERE user_id = :u"), {"u": OWNER_ID})
    async with env.session_factory() as db, db.begin():
        purged = await db.scalar(select(func.purge_user_audit(env.other_id)))
        assert purged >= 1
        assert await db.scalar(select(func.count()).select_from(AuditLog).where(
            AuditLog.user_id == OWNER_ID)) >= 1  # fmt: skip
    async with env.session_factory() as db:
        with pytest.raises(DBAPIError, match="append-only"):  # the purge setting doesn't linger
            async with db.begin():
                await db.execute(text("DELETE FROM audit_log WHERE user_id = :u"), {"u": OWNER_ID})
