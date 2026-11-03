from datetime import date, timedelta

import pytest
from sqlalchemy import select

import app.execution.executor as executor_module
from app.db.models import ActionExecution, ActionProposal, AuditLog
from app.execution.actions import (
    ActionKind,
    CreatePeriod,
    CreateTask,
    MarkDone,
    SetDue,
    UpsertHabitDay,
    WritePlan,
)
from app.execution.executor import Executor, take_snapshot
from app.execution.proposals import ProposalService, utcnow
from tests.factories import specs
from tests.fake_notion import FakeNotion
from tests.workflow_support import OWNER_ID

SPECS = specs()
TASKS, HABITS, WEEKS, MONTHS = (SPECS[n] for n in ("tasks", "habits", "weeks", "months"))


def open_task(fake: FakeNotion, **overrides) -> str:
    values = {"title": "Write tests", "status": "Next", "due": date(2026, 10, 31), **overrides}
    return fake.seed(TASKS, values)


async def approved(session_factory, kind, payload, *, clock=utcnow, **kwargs):
    async with session_factory() as session, session.begin():
        service = ProposalService(session, user_id=OWNER_ID, clock=clock)
        proposal, _ = await service.propose(kind, payload, reason="test", **kwargs)
        await service.approve(proposal.id)
        return proposal.id


def executor(session_factory, fake, writes=True) -> Executor:
    return Executor(session_factory, fake, SPECS, user_id=OWNER_ID, writes_enabled=writes)


async def proposal_row(session_factory, proposal_id):
    async with session_factory() as session:
        return await session.get(ActionProposal, proposal_id)


async def test_mark_done_writes_verifies_and_audits(session_factory):
    fake = FakeNotion()
    task = open_task(fake)
    proposal_id = await approved(
        session_factory,
        ActionKind.TASK_MARK_DONE,
        MarkDone(page_id=task, completed_on=date(2026, 11, 1)),
    )
    [result] = await executor(session_factory, fake).execute_approved()
    assert result.outcome == "verified" and result.page_id == task
    page = fake.pages[task]["properties"]
    assert page["Status"]["select"]["name"] == "Done"
    assert page["Completed"]["date"]["start"] == "2026-11-01"
    row = await proposal_row(session_factory, proposal_id)
    assert row.status == "executed" and row.result_page_id == task
    async with session_factory() as session:
        assert (await session.scalar(select(ActionExecution.outcome))) == "verified"
        events = list(await session.scalars(select(AuditLog.event).order_by(AuditLog.id)))
    assert events == ["proposal.created", "proposal.approved", "execution.verified"]


async def test_already_applied_changes_are_a_noop(session_factory):
    fake = FakeNotion()
    task = open_task(fake, status="Done", completed=date(2026, 11, 1))
    await approved(
        session_factory,
        ActionKind.TASK_MARK_DONE,
        MarkDone(page_id=task, completed_on=date(2026, 11, 1)),
    )
    [result] = await executor(session_factory, fake).execute_approved()
    assert result.outcome == "noop" and fake.update_calls == 0


async def test_page_changed_since_proposal_is_stale_and_untouched(session_factory):
    fake = FakeNotion()
    task = open_task(fake)
    week = fake.seed(WEEKS, {"title": "W", "start": date(2026, 11, 2), "end": date(2026, 11, 8)})
    month = fake.seed(
        MONTHS, {"title": "Nov", "start": date(2026, 11, 1), "end": date(2026, 11, 30)}
    )
    payload = SetDue(page_id=task, new_due=date(2026, 11, 3), decision="carry_forward",
                     week_page_id=week, month_page_id=month)  # fmt: skip
    snapshot, edited_at = await take_snapshot(fake, SPECS, ActionKind.TASK_SET_DUE, payload)
    proposal_id = await approved(
        session_factory,
        ActionKind.TASK_SET_DUE,
        payload,
        before_snapshot=snapshot,
        target_last_edited_at=edited_at,
    )
    fake.edit(task, TASKS, {"due": date(2026, 11, 5)})  # the owner rescheduled it by hand
    [result] = await executor(session_factory, fake).execute_approved()
    assert result.outcome == "stale" and "due" in result.detail
    assert fake.update_calls == 0
    assert (await proposal_row(session_factory, proposal_id)).status == "stale"


async def test_kill_switch_blocks_every_write(session_factory):
    fake = FakeNotion()
    task = open_task(fake)
    proposal_id = await approved(
        session_factory,
        ActionKind.TASK_MARK_DONE,
        MarkDone(page_id=task, completed_on=date(2026, 11, 1)),
    )
    [result] = await executor(session_factory, fake, writes=False).execute_approved()
    assert result.outcome == "blocked"
    assert fake.update_calls == 0
    assert (await proposal_row(session_factory, proposal_id)).status == "approved"


async def test_carry_forward_into_a_new_week_runs_the_dependency_first(session_factory):
    fake = FakeNotion()
    task = open_task(fake)
    month = fake.seed(
        MONTHS, {"title": "Nov", "start": date(2026, 11, 1), "end": date(2026, 11, 30)}
    )
    week_id = await approved(
        session_factory, ActionKind.WEEK_CREATE,
        CreatePeriod(title="2 – 8 Nov", start=date(2026, 11, 2), end=date(2026, 11, 8)),
    )  # fmt: skip
    move_id = await approved(
        session_factory, ActionKind.TASK_SET_DUE,
        SetDue(page_id=task, new_due=date(2026, 11, 3), decision="carry_forward",
               week_proposal_id=week_id, month_page_id=month),
    )  # fmt: skip
    results = await executor(session_factory, fake).execute_approved()
    assert [r.proposal_id for r in results] == [week_id, move_id]
    assert [r.outcome for r in results] == ["verified", "verified"]
    new_week = results[0].page_id
    assert fake.pages[task]["properties"]["Week"]["relation"] == [{"id": new_week}]
    assert fake.pages[task]["properties"]["Due"]["date"]["start"] == "2026-11-03"


async def test_a_rejected_dependency_fails_the_dependent_action(session_factory):
    fake = FakeNotion()
    task = open_task(fake)
    month = fake.seed(
        MONTHS, {"title": "Nov", "start": date(2026, 11, 1), "end": date(2026, 11, 30)}
    )
    async with session_factory() as session, session.begin():
        service = ProposalService(session, user_id=OWNER_ID)
        week, _ = await service.propose(
            ActionKind.WEEK_CREATE,
            CreatePeriod(title="W", start=date(2026, 11, 2), end=date(2026, 11, 8)), reason="r",
        )  # fmt: skip
        await service.reject(week.id)
    move_id = await approved(
        session_factory, ActionKind.TASK_SET_DUE,
        SetDue(page_id=task, new_due=date(2026, 11, 3), decision="carry_forward",
               week_proposal_id=week.id, month_page_id=month),
    )  # fmt: skip
    [result] = await executor(session_factory, fake).execute_approved()
    assert result.outcome == "failed" and "rejected" in result.detail
    assert (await proposal_row(session_factory, move_id)).status == "failed"


async def test_habit_upsert_finds_the_hand_typed_row_and_keeps_its_title(session_factory):
    fake = FakeNotion()
    row = fake.seed(HABITS, {"title": "1/11/26", "reading": False})
    payload = UpsertHabitDay(
        day=date(2026, 11, 1), checks={"reading": True, "dsa": True}, sleep=7.5
    )
    await approved(session_factory, ActionKind.HABIT_UPSERT_DAY, payload)
    [result] = await executor(session_factory, fake).execute_approved()
    assert result.outcome == "verified" and result.page_id == row and fake.create_calls == 0
    props = fake.pages[row]["properties"]
    assert props["Date"]["title"][0]["plain_text"] == "1/11/26"
    assert props["Reading"]["checkbox"] is True and props["Sleep"]["number"] == 7.5
    # The same values proposed the next day change nothing.
    tomorrow = lambda: utcnow() + timedelta(days=1)  # noqa: E731
    await approved(session_factory, ActionKind.HABIT_UPSERT_DAY, payload, clock=tomorrow)
    [again] = await executor(session_factory, fake).execute_approved()
    assert again.outcome == "noop" and fake.update_calls == 1


async def test_new_habit_day_is_created_as_dd_mm_yyyy(session_factory):
    fake = FakeNotion()
    await approved(
        session_factory, ActionKind.HABIT_UPSERT_DAY, UpsertHabitDay(day=date(2026, 11, 2))
    )
    [result] = await executor(session_factory, fake).execute_approved()
    assert result.outcome == "verified"
    assert (
        fake.pages[result.page_id]["properties"]["Date"]["title"][0]["plain_text"] == "02/11/2026"
    )


async def test_retry_after_an_ambiguous_create_never_duplicates(session_factory):
    fake = FakeNotion()
    fake.fail_after_create = True
    proposal_id = await approved(
        session_factory,
        ActionKind.TASK_CREATE,
        CreateTask(title="Book mock interview", status="next", due=date(2026, 11, 4)),
    )
    [first] = await executor(session_factory, fake).execute_approved()
    assert first.outcome == "failed"
    async with session_factory() as session, session.begin():
        await ProposalService(session, user_id=OWNER_ID).retry(proposal_id)
    [second] = await executor(session_factory, fake).execute_approved()
    assert second.outcome == "noop" and fake.create_calls == 1
    titles = [p["properties"]["Task"]["title"][0]["plain_text"] for p in fake.pages.values()]
    assert titles.count("Book mock interview") == 1
    async with session_factory() as session:
        attempts = list(
            await session.scalars(select(ActionExecution.outcome).order_by(ActionExecution.attempt))
        )
    assert attempts == ["failed", "noop"]


async def test_write_that_does_not_stick_is_reported_as_mismatch(session_factory):
    fake = FakeNotion()
    fake.ignore_on_write = {"Completed"}
    task = open_task(fake)
    await approved(
        session_factory,
        ActionKind.TASK_MARK_DONE,
        MarkDone(page_id=task, completed_on=date(2026, 11, 1)),
    )
    [result] = await executor(session_factory, fake).execute_approved()
    assert result.outcome == "mismatch" and "completed" in result.detail


async def test_writes_outside_the_allowlist_are_refused(session_factory, monkeypatch):
    fake = FakeNotion()
    task = open_task(fake)
    await approved(
        session_factory,
        ActionKind.TASK_MARK_DONE,
        MarkDone(page_id=task, completed_on=date(2026, 11, 1)),
    )
    monkeypatch.setattr(
        executor_module,
        "build_plan",
        lambda *a, **k: WritePlan("tasks", task, {"career_evidence": ["x"]}),
    )
    [result] = await executor(session_factory, fake).execute_approved()
    assert result.outcome == "failed" and "not allowed" in result.detail
    assert fake.update_calls == 0


@pytest.mark.parametrize("status", ["pending", "rejected"])
async def test_only_approved_proposals_execute(session_factory, status):
    fake = FakeNotion()
    task = open_task(fake)
    async with session_factory() as session, session.begin():
        service = ProposalService(session, user_id=OWNER_ID)
        proposal, _ = await service.propose(
            ActionKind.TASK_MARK_DONE,
            MarkDone(page_id=task, completed_on=date(2026, 11, 1)),
            reason="r",
        )
        if status == "rejected":
            await service.reject(proposal.id)
    assert await executor(session_factory, fake).execute_approved() == []
    result = await executor(session_factory, fake).execute_one(proposal.id)
    assert result.outcome == "skipped" and fake.update_calls == 0


def drifted(fake: FakeNotion, *, drop_option: str | None = None, drop_property: str | None = None):
    """The owner changed the Tasks database in Notion after it was mapped."""
    from app.demo.seed import schema_for

    fake.schemas = {spec.data_source_id: schema_for(spec) for spec in SPECS.values()}
    properties = fake.schemas[TASKS.data_source_id]["properties"]
    if drop_option:
        status = properties["Status"]["select"]
        status["options"] = [o for o in status["options"] if o["name"] != drop_option]
    if drop_property:
        del properties[drop_property]


async def test_writes_to_a_drifted_field_are_refused(session_factory):
    fake = FakeNotion()
    task = open_task(fake)
    drifted(fake, drop_option="Done")  # renamed in Notion, say
    proposal_id = await approved(
        session_factory, ActionKind.TASK_MARK_DONE,
        MarkDone(page_id=task, completed_on=date(2026, 11, 1)),
    )  # fmt: skip
    [result] = await executor(session_factory, fake).execute_approved()
    assert result.outcome == "failed" and "Status: missing_option" in result.detail
    assert fake.update_calls == 0
    assert (await proposal_row(session_factory, proposal_id)).status == "failed"
    async with session_factory() as session:
        execution = await session.scalar(select(ActionExecution))
    assert execution.error == {"type": "schema_drift"}


async def test_fields_that_still_match_are_written(session_factory):
    fake = FakeNotion()
    task = open_task(fake)
    drifted(fake, drop_option="Done", drop_property="Priority")
    await approved(
        session_factory, ActionKind.TASK_SET_DUE,
        SetDue(page_id=task, new_due=date(2026, 11, 5), decision="reschedule"),
    )  # fmt: skip
    [result] = await executor(session_factory, fake).execute_approved()
    assert result.outcome == "verified"
