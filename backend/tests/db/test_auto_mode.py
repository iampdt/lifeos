"""Automatic mode: allowlisted kinds run without approval and can be undone; everything else
still waits for approval."""

import uuid
from datetime import UTC, datetime

import httpx
from sqlalchemy import select

from app.api.main import create_app
from app.core.config import Settings
from app.db.models import ActionProposal
from app.execution.actions import ActionKind
from app.llm.mock import MockLLM
from app.permissions.modes import save_modes
from app.permissions.policy import Mode
from tests.fake_notion import FakeNotion
from tests.workflow_support import OWNER_ID, SPECS, make_runner, make_services, pages_in, read

SUNDAY_EVENING = datetime(2026, 11, 1, 15, 0, tzinfo=UTC)  # 20:30 in Asia/Kolkata
AUTH = {"Authorization": "Bearer tok"}


def goal_workspace() -> tuple[FakeNotion, str]:
    fake = FakeNotion()
    launch = fake.seed(SPECS["goals"], {"title": "Launch v1", "status": "Active", "current": 3,
                                        "target": 10})  # fmt: skip
    return fake, launch


async def review_with_progress(runner, figure: str):
    view = await runner.start("weekly_review")
    [goal] = view.waiting_for["goals"]
    return await runner.resume(uuid.UUID(view.id), {"goals": {goal["ref"]: {"current": figure}}})


async def test_an_automatic_kind_is_applied_without_asking(session_factory):
    fake, launch = goal_workspace()
    runner, deps = make_runner(session_factory, fake, None, now=SUNDAY_EVENING)
    deps.modes[ActionKind.GOAL_SET_CURRENT] = Mode.AUTO
    view = await review_with_progress(runner, "5")
    # Only the week's own changes wait for approval; the progress figure is already written.
    assert {c["kind"] for c in view.waiting_for["proposals"]} == {"week.create"}
    assert read(fake, "goals", launch)["current"] == 5
    view = await runner.resume(uuid.UUID(view.id), {"approve": "all"})
    assert view.summary["execution"] == {"verified": 2}
    assert any("applied automatically" in note for note in view.summary["notes"])
    async with session_factory() as db:
        row = await db.scalar(
            select(ActionProposal).where(ActionProposal.kind == "goal.set_current")
        )
    assert row.auto_applied and row.status == "executed" and row.restore == {"current": 3}


async def test_kinds_left_on_approval_still_wait(session_factory):
    fake, launch = goal_workspace()
    runner, _ = make_runner(session_factory, fake, None, now=SUNDAY_EVENING)
    view = await review_with_progress(runner, "5")
    assert "goal.set_current" in {c["kind"] for c in view.waiting_for["proposals"]}
    assert read(fake, "goals", launch)["current"] == 3


def client_for(session_factory, fake) -> httpx.AsyncClient:
    settings = Settings(_env_file=None, lifeos_api_token="tok")
    services, _ = make_services(session_factory, fake, MockLLM(), now=SUNDAY_EVENING,
                                settings=settings)  # fmt: skip
    transport = httpx.ASGITransport(app=create_app(services))
    return httpx.AsyncClient(transport=transport, base_url="https://t", headers=AUTH)


async def automatic_change(session_factory, fake) -> str:
    await save_modes(session_factory, OWNER_ID, {ActionKind.GOAL_SET_CURRENT: Mode.AUTO})
    runner, deps = make_runner(session_factory, fake, None, now=SUNDAY_EVENING)
    deps.modes[ActionKind.GOAL_SET_CURRENT] = Mode.AUTO
    await review_with_progress(runner, "5")
    async with session_factory() as db:
        return str(await db.scalar(
            select(ActionProposal.id).where(ActionProposal.kind == "goal.set_current")
        ))  # fmt: skip


async def test_an_automatic_change_is_listed_and_undone_once(session_factory):
    fake, launch = goal_workspace()
    proposal_id = await automatic_change(session_factory, fake)
    async with client_for(session_factory, fake) as client:
        [card] = (await client.get("/api/v1/approvals")).json()["automatic"]
        assert card["id"] == proposal_id and card["undoable"] and card["auto_applied"]
        undone = await client.post(f"/api/v1/proposals/{proposal_id}/undo")
        assert undone.json()["outcome"] == "verified"
        assert read(fake, "goals", launch)["current"] == 3
        again = await client.post(f"/api/v1/proposals/{proposal_id}/undo")
        assert again.status_code == 409 and again.json()["error"]["code"] == "not_undoable"
        [card] = (await client.get("/api/v1/approvals")).json()["automatic"]
        assert card["status"] == "undone" and not card["undoable"]


async def test_undo_never_overwrites_a_later_edit(session_factory):
    fake, launch = goal_workspace()
    proposal_id = await automatic_change(session_factory, fake)
    fake.edit(launch, SPECS["goals"], {"current": 7})  # the owner updated it in Notion
    async with client_for(session_factory, fake) as client:
        refused = await client.post(f"/api/v1/proposals/{proposal_id}/undo")
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "changed_since"
    assert read(fake, "goals", launch)["current"] == 7


async def test_a_new_scorecard_row_is_undone_by_clearing_it(session_factory):
    fake = FakeNotion()
    runner, deps = make_runner(session_factory, fake, None, now=SUNDAY_EVENING)
    deps.modes[ActionKind.HABIT_UPSERT_DAY] = Mode.AUTO
    view = await runner.start("evening_checkin", inputs={"plan_tomorrow": False})
    view = await runner.resume(
        uuid.UUID(view.id), {"habits": {"dsa": True}, "metrics": {"sleep": 7}}
    )
    assert view.status == "completed"  # nothing else needed approval
    [row] = pages_in(fake, "habits").items()
    page_id, values = row
    assert values["dsa"] is True and values["sleep"] == 7
    async with session_factory() as db:
        proposal = await db.scalar(select(ActionProposal))
    result = await deps.executor("user").undo(proposal.id)
    assert result.outcome == "verified"
    after = read(fake, "habits", page_id)
    assert after["dsa"] is False and after["sleep"] is None
    assert after["title"] == values["title"]  # the row and its day stay


async def test_auto_is_offered_only_for_allowlisted_kinds(session_factory):
    async with client_for(session_factory, FakeNotion()) as client:
        settings = (await client.get("/api/v1/settings")).json()
        assert settings["auto_kinds"] == [
            "book.update_progress", "goal.set_current", "habit.upsert_day",
        ]  # fmt: skip
        ok = await client.put(
            "/api/v1/settings/modes", json={"modes": {"habit.upsert_day": "auto"}}
        )
        assert ok.json()["modes"]["habit.upsert_day"] == "auto"
        refused = await client.put(
            "/api/v1/settings/modes", json={"modes": {"task.set_due": "auto"}}
        )
        assert refused.status_code == 422
