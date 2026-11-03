"""A workspace that looks nothing like the owner's: Notion's default status options, a date
property on the habits table, its own habits and metrics, and no weekly, monthly, goal or reading
databases. The workflows run on it unchanged, and an account can save it as its mapping."""

import uuid
from datetime import UTC, date, datetime

import httpx
import pytest
from cryptography.fernet import Fernet
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import select

from app.api.main import Services, create_app
from app.core.config import Settings
from app.core.crypto import TokenCipher
from app.db.models import Workspace
from app.demo.notion import schema_for
from app.llm.mock import MockLLM
from app.notion.connections import NotionConnections
from app.notion.mapping import DataSourceSpec, PropertySpec
from app.notion.oauth import NotionOAuth, TokenGrant
from app.workflows.contexts import Contexts
from app.workflows.deps import RuleSettings, WorkflowDeps
from app.workflows.runner import WorkflowRunner
from app.workspaces.drift import check_drift
from app.workspaces.store import AccountWorkspaces, mapping_json
from tests.fake_notion import FakeNotion
from tests.notion_http import NotionOverHTTP
from tests.workflow_support import (
    OWNER_ID,
    add_user,
    make_services,
    router,
    set_password,
)
from tests.workflow_support import SPECS as OWNER_SPECS

NOW = datetime(2026, 11, 4, 14, 0, tzinfo=UTC)  # Wednesday evening in Asia/Kolkata
TODAY = date(2026, 11, 4)
PASSWORD = "a long test password"


def P(name: str, type_: str, **extra) -> PropertySpec:  # noqa: N802
    return PropertySpec(name=name, type=type_, **extra)


TASKS = DataSourceSpec(
    logical_name="tasks",
    data_source_id="ds-todo",
    properties={
        "title": P("Name", "title"),
        "status": P("Status", "status",
                    values={"next": "Not started", "doing": "In progress", "done": "Done"}),
        "due": P("Due date", "date"),
    },
)  # fmt: skip
HABITS = DataSourceSpec(
    logical_name="habits",
    data_source_id="ds-days",
    properties={
        "title": P("Name", "title"),
        "day": P("Date", "date"),
        "meditate": P("Meditate", "checkbox", description="Meditated for 10 minutes"),
        "walk": P("Walk", "checkbox"),
        "steps": P("Steps", "number", unit="steps"),
    },
)
SPECS = {"tasks": TASKS, "habits": HABITS}


def workspace() -> FakeNotion:
    fake = FakeNotion()
    for spec in SPECS.values():
        fake.schemas[spec.data_source_id] = schema_for(spec)
    fake.seed(TASKS, {"title": "Ship the invoice", "status": "In progress", "due": TODAY})
    fake.seed(TASKS, {"title": "Plan the offsite", "status": "Not started"})
    fake.seed(TASKS, {"title": "Old idea", "status": "Done", "due": date(2026, 10, 1)})
    return fake


def deps(session_factory, fake, mock=None, user_id=OWNER_ID, limit=2) -> WorkflowDeps:
    return WorkflowDeps(
        session_factory, fake, SPECS, router(mock), writes_enabled=True,
        user_id=user_id, clock=lambda: NOW, rules=RuleSettings(max_major_per_day=limit),
    )  # fmt: skip


def pages(fake: FakeNotion, spec: DataSourceSpec) -> list[dict]:
    return [p for p in fake.pages.values() if p["parent"]["data_source_id"] == spec.data_source_id]


def prop(page: dict, name: str):
    value = page["properties"][name]
    return value.get(value["type"])


async def test_the_evening_checkin_runs_on_a_generic_workspace(session_factory):
    fake = workspace()
    mock = MockLLM()
    mock.queue("DayPlan", {"picks": [{"ref": "T1", "reason": "Next up"}], "note": ""})
    workflow = deps(session_factory, fake, mock)
    runner = WorkflowRunner(workflow, InMemorySaver())
    view = await runner.start("evening_checkin")
    question = view.waiting_for
    assert question["prompt"] == (
        "What did you finish today? Mention habits, steps, what you learned and anything new "
        "to capture."
    )
    assert [h["key"] for h in question["habits"]["fields"]] == ["meditate", "walk"]
    assert question["habits"]["metrics"] == [{"key": "steps", "name": "Steps", "unit": "steps"}]
    assert [t["title"] for t in question["tasks"]] == ["Ship the invoice"]

    answer = {"done_refs": ["T1"], "habits": {"meditate": True}, "metrics": {"steps": 9000}}
    view = await runner.resume(uuid.UUID(view.id), answer)
    kinds = sorted(card["kind"] for card in view.waiting_for["proposals"])
    assert kinds == ["habit.upsert_day", "task.mark_done", "task.set_due"]  # no week/month pages
    move = next(c for c in view.waiting_for["proposals"] if c["kind"] == "task.set_due")
    assert move["rule_ids"] == ["R1"] and move["depends_on"] == []

    view = await runner.resume(uuid.UUID(view.id), {"approve": "all"})
    assert view.status == "completed" and view.summary["execution"] == {"verified": 3}
    assert view.summary["habits"] == {"Meditate": True}
    assert view.summary["metrics"] == ["Steps 9000 steps"]
    tasks = {prop(p, "Name")[0]["plain_text"]: p for p in pages(fake, TASKS)}
    assert prop(tasks["Ship the invoice"], "Status")["name"] == "Done"
    assert prop(tasks["Plan the offsite"], "Due date")["start"] == "2026-11-05"
    [day] = pages(fake, HABITS)
    assert prop(day, "Date")["start"] == "2026-11-04"
    assert prop(day, "Name")[0]["plain_text"] == "2026-11-04"  # ISO title by default
    assert prop(day, "Meditate") is True and prop(day, "Steps") == 9000


async def test_the_weekly_review_and_morning_briefing_skip_missing_modules(session_factory):
    fake = workspace()
    fake.seed(TASKS, {"title": "Late thing", "status": "Not started", "due": date(2026, 11, 2)})
    runner = WorkflowRunner(deps(session_factory, fake), InMemorySaver())
    weekly = await runner.start("weekly_review")
    assert weekly.status == "waiting_input"
    # Open at the end of the week (it's Wednesday): both carry a decision.
    overdue = {t["title"] for t in weekly.waiting_for["overdue"]}
    assert overdue == {"Late thing", "Ship the invoice"}
    assert weekly.waiting_for["inbox"] == []  # this workspace has no Inbox status
    done = await runner.resume(uuid.UUID(weekly.id), {"accept": True})
    kinds = [c["kind"] for c in (done.waiting_for or {}).get("proposals", [])]
    assert "week.close" not in kinds and "week.create" not in kinds
    morning = await runner.start("morning_briefing")
    assert morning.status in ("completed", "waiting_approval")
    assert morning.error is None


async def test_the_daily_limit_comes_from_the_workspace(session_factory):
    fake = workspace()
    for i in range(2):
        fake.seed(TASKS, {"title": f"Big {i}", "status": "Not started", "due": TODAY})
    runner = WorkflowRunner(deps(session_factory, fake, limit=2), InMemorySaver())
    view = await runner.start("morning_briefing")
    # Three major tasks today against a limit of two: the briefing proposes trimming one.
    proposals = (view.waiting_for or {}).get("proposals", [])
    assert [c["kind"] for c in proposals] == ["task.set_due"]


# --- saving the mapping as an account ---------------------------------------------------------


@pytest.fixture
async def account(session_factory):
    """An account connected (over HTTP) to the generic workspace, with Notion OAuth enabled."""
    await set_password(session_factory, PASSWORD)
    user = await add_user(session_factory, "member@example.com", password=PASSWORD)
    fake = workspace()
    server = NotionOverHTTP(fake)
    transport = httpx.MockTransport(server.handler)
    base = "https://notion.test/v1"
    oauth = NotionOAuth("id", "secret", "https://app.test/cb", base_url=base,
                        http=httpx.AsyncClient(transport=transport))  # fmt: skip
    connections = NotionConnections(session_factory, TokenCipher(Fernet.generate_key().decode()),
                                    oauth, base_url=base, transport=transport,
                                    requests_per_second=1000)  # fmt: skip
    await connections.save(user.id, TokenGrant.from_response(server._grant()))
    owner_services, _ = make_services(session_factory, FakeNotion(), MockLLM(), now=NOW)

    def account_deps(notion, specs, user_id, tz, rules, is_owner):
        return WorkflowDeps(session_factory, notion, specs, router(MockLLM()), writes_enabled=True,
                            user_id=user_id, tz=tz, rules=rules, clock=lambda: NOW)  # fmt: skip

    accounts = AccountWorkspaces(session_factory, connections, account_deps)
    contexts = Contexts(owner_services.contexts._owner_deps, InMemorySaver(),
                        account_deps=accounts.deps_for)  # fmt: skip
    services = Services(Settings(_env_file=None), session_factory, contexts,
                        clock=lambda: NOW, connections=connections)  # fmt: skip
    transport = httpx.ASGITransport(app=create_app(services))
    async with httpx.AsyncClient(transport=transport, base_url="https://t") as client:
        credentials = {"email": "member@example.com", "password": PASSWORD}
        login = await client.post("/api/v1/auth/login", json=credentials)
        client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
        yield client, user, fake, services


BODY = {
    "modules": mapping_json(SPECS),
    "settings": {"timezone": "Europe/Berlin", "max_major_per_day": 2},
}


async def test_an_account_saves_its_mapping_and_gets_a_working_workspace(account, session_factory):
    client, user, fake, _ = account
    me = (await client.get("/api/v1/auth/me")).json()
    assert me["workspace"] is False
    assert (await client.get("/api/v1/workspace")).json()["source"] is None

    saved = await client.put("/api/v1/workspace", json=BODY)
    assert saved.status_code == 200, saved.text
    assert saved.json()["source"] == "account" and set(saved.json()["modules"]) == {
        "tasks",
        "habits",
    }
    async with session_factory() as db:
        row = await db.get(Workspace, user.id)
    assert row.timezone == "Europe/Berlin" and row.max_major_per_day == 2

    assert (await client.get("/api/v1/auth/me")).json()["workspace"] is True
    tasks = (await client.get("/api/v1/tasks")).json()  # through the account's own client
    assert sorted(t["title"] for t in tasks) == ["Plan the offsite", "Ship the invoice"]
    settings = (await client.get("/api/v1/settings")).json()
    assert settings["timezone"] == "Europe/Berlin"
    run = await client.post("/api/v1/workflows/evening_checkin/runs", json={})
    assert run.status_code == 201 and run.json()["status"] == "waiting_input"


async def test_a_mapping_that_doesnt_fit_is_refused_with_every_problem(account):
    client, _, _, _ = account
    broken = mapping_json(SPECS)
    del broken["tasks"]["properties"]["status"]["values"]["done"]  # nothing means "done"
    broken["habits"]["properties"]["title"]["type"] = "rich_text"
    response = await client.put("/api/v1/workspace", json={**BODY, "modules": broken})
    assert response.status_code == 422
    problems = {(d["module"], d["field"]) for d in response.json()["error"]["details"]}
    assert problems == {("tasks", "status"), ("habits", "title")}

    renamed = mapping_json(SPECS)
    renamed["tasks"]["properties"]["due"]["name"] = "Deadline"  # not in the live database
    response = await client.put("/api/v1/workspace", json={**BODY, "modules": renamed})
    assert response.status_code == 422
    [detail] = response.json()["error"]["details"]
    assert detail["module"] == "tasks" and "Deadline" in detail["problem"]

    elsewhere = mapping_json(SPECS)
    elsewhere["habits"]["data_source_id"] = "ds-not-shared"
    response = await client.put("/api/v1/workspace", json={**BODY, "modules": elsewhere})
    assert response.json()["error"]["details"][0]["problem"] == "LifeOS can't see this database"

    bad_zone = await client.put(
        "/api/v1/workspace", json={**BODY, "settings": {"timezone": "Mars/Base"}}
    )
    assert bad_zone.status_code == 422


async def test_the_owners_environment_workspace_is_read_only(session_factory):
    settings = Settings(_env_file=None, lifeos_api_token="tok")
    services, _ = make_services(
        session_factory, FakeNotion(), MockLLM(), now=NOW, settings=settings,
        environment_specs=OWNER_SPECS,
    )  # fmt: skip
    transport = httpx.ASGITransport(app=create_app(services))
    auth = {"Authorization": "Bearer tok"}
    async with httpx.AsyncClient(transport=transport, base_url="https://t") as client:
        view = (await client.get("/api/v1/workspace", headers=auth)).json()
        assert view["source"] == "environment" and view["editable"] is False
        assert "tasks" in view["modules"] and view["can_import"] is False  # not connected
        refused = await client.put("/api/v1/workspace", json=BODY, headers=auth)
        assert refused.status_code == 404  # this deployment has no Notion connections
        catalogue = (await client.get("/api/v1/workspace/modules", headers=auth)).json()
        assert catalogue["tasks"]["fields"]["status"]["needs"] == ["next", "done"]
    async with services.session_factory() as db:
        assert (await db.scalars(select(Workspace))).all() == []


async def test_onboarding_with_the_suggestion_as_is_gives_a_working_workspace(account):
    client, _, fake, _ = account
    proposal = (await client.get("/api/v1/workspace/suggestion")).json()
    assert {s["title"] for s in proposal["data_sources"]} == {"Tasks", "Habits"}
    assert set(proposal["modules"]) == {"tasks", "habits"}
    tasks = proposal["modules"]["tasks"]["properties"]
    assert tasks["status"]["values"] == {"next": "Not started", "doing": "In progress",
                                         "done": "Done"}  # fmt: skip
    habits = proposal["modules"]["habits"]["properties"]
    assert habits["day"]["name"] == "Date" and habits["steps"]["unit"] == "steps"
    assert {"meditate", "walk"} <= set(habits)
    assert proposal["schemas"]["ds-todo"]["title"] == "Tasks"

    body = {"modules": proposal["modules"], "settings": {"timezone": "Asia/Kolkata"}}
    saved = await client.put("/api/v1/workspace", json=body)
    assert saved.status_code == 200, saved.text
    assert (await client.get("/api/v1/auth/me")).json()["workspace"] is True

    one = await client.get("/api/v1/workspace/data-sources/ds-days", params={"module": "habits"})
    assert one.json()["suggestion"]["properties"]["day"]["name"] == "Date"
    assert {p["name"] for p in one.json()["schema"]["properties"]} >= {"Meditate", "Steps"}
    missing = await client.get("/api/v1/workspace/data-sources/nope", params={"module": "tasks"})
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "not_shared"


async def test_the_daily_drift_check_pauses_what_changed_until_it_is_set_up_again(account):
    client, user, fake, services = account
    assert (await client.put("/api/v1/workspace", json=BODY)).status_code == 200

    async def check():
        paused = await check_drift(services.session_factory, services.connections, user.id,
                                   lambda: NOW)  # fmt: skip
        await services.contexts.forget(user.id)
        return paused

    habits = fake.schemas["ds-days"]["properties"]
    habits["Step count"] = {**habits.pop("Steps"), "name": "Step count"}  # renamed in Notion
    assert await check() == ["habits"]
    view = (await client.get("/api/v1/workspace")).json()
    assert view["paused"] == ["habits"] and "'Steps'" in view["drift"]["habits"][0]
    run = (await client.post("/api/v1/workflows/evening_checkin/runs", json={})).json()
    assert run["waiting_for"]["habits"] is None  # the rest of the workspace keeps working

    tasks = fake.schemas["ds-todo"]["properties"]
    tasks["Deadline"] = {**tasks.pop("Due date"), "name": "Deadline"}
    assert await check() == ["habits", "tasks"]
    assert (await client.get("/api/v1/auth/me")).json()["workspace"] is False

    habits["Steps"] = {**habits.pop("Step count"), "name": "Steps"}
    tasks["Due date"] = {**tasks.pop("Deadline"), "name": "Due date"}
    saved = (await client.put("/api/v1/workspace", json=BODY)).json()
    assert saved["paused"] == [] and saved["drift"] is None
    assert (await client.get("/api/v1/auth/me")).json()["workspace"] is True
