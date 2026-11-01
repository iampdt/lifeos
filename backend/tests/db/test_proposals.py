from datetime import date, timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.db.models import ActionProposal, AuditLog
from app.execution.actions import ActionKind, CreateTask, MarkDone, SetStatus
from app.execution.proposals import ProposalError, ProposalService, utcnow
from app.permissions.policy import DEFAULT_MODES, Mode

DONE = MarkDone(page_id="task-1", completed_on=date(2026, 11, 1))


async def propose(session_factory, payload=DONE, kind=ActionKind.TASK_MARK_DONE, **kwargs):
    async with session_factory() as session, session.begin():
        return await ProposalService(session).propose(kind, payload, reason="test", **kwargs)


async def test_propose_creates_a_pending_proposal_and_audits_it(session_factory):
    proposal, created = await propose(session_factory, rule_ids=["R4"])
    assert created and proposal.status == "pending"
    assert (proposal.target_data_source, proposal.target_page_id) == ("tasks", "task-1")
    async with session_factory() as session:
        assert list(await session.scalars(select(AuditLog.event))) == ["proposal.created"]


async def test_identical_proposals_on_the_same_day_collapse(session_factory):
    first, created_first = await propose(session_factory)
    second, created_second = await propose(session_factory)
    assert created_first and not created_second and first.id == second.id
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(ActionProposal)) == 1


async def test_the_same_suggestion_on_a_later_day_is_a_new_proposal(session_factory):
    first, _ = await propose(session_factory)
    async with session_factory() as session, session.begin():
        later = ProposalService(session, clock=lambda: utcnow() + timedelta(days=2))
        second, created = await later.propose(ActionKind.TASK_MARK_DONE, DONE, reason="again")
    assert created and second.id != first.id


async def test_payload_must_match_the_kind(session_factory):
    with pytest.raises(ProposalError):
        await propose(session_factory, payload=SetStatus(page_id="t", status="next"))


async def test_approval_with_valid_edits(session_factory):
    proposal, _ = await propose(session_factory)
    async with session_factory() as session, session.begin():
        approved = await ProposalService(session).approve(
            proposal.id, edits={"completed_on": "2026-10-31"}, note="finished it yesterday"
        )
    assert approved.status == "approved"
    assert approved.effective_payload["completed_on"] == "2026-10-31"
    assert approved.payload["completed_on"] == "2026-11-01"  # the original is kept


async def test_edits_that_break_validation_or_retarget_are_refused(session_factory):
    proposal, _ = await propose(session_factory)
    for edits in ({"completed_on": "not a date"}, {"page_id": "someone-else"}):
        async with session_factory() as session:
            with pytest.raises(ProposalError):
                await ProposalService(session).approve(proposal.id, edits=edits)


async def test_cancelling_is_flagged_important(session_factory):
    proposal, _ = await propose(
        session_factory,
        kind=ActionKind.TASK_SET_STATUS,
        payload=SetStatus(page_id="t", status="cancelled"),
    )
    assert proposal.importance == "important"


async def test_rejected_proposals_cannot_then_be_approved(session_factory):
    proposal, _ = await propose(session_factory)
    async with session_factory() as session, session.begin():
        await ProposalService(session).reject(proposal.id, note="not done yet")
    async with session_factory() as session:
        with pytest.raises(ProposalError):
            await ProposalService(session).approve(proposal.id)


async def test_expired_proposals_cannot_be_approved(session_factory):
    proposal, _ = await propose(session_factory, ttl=timedelta(hours=1))
    later = lambda: utcnow() + timedelta(hours=2)  # noqa: E731
    async with session_factory() as session:
        with pytest.raises(ProposalError):
            await ProposalService(session, clock=later).approve(proposal.id)
    async with session_factory() as session, session.begin():
        assert await ProposalService(session, clock=later).expire_due() == 1
    async with session_factory() as session:
        assert (await session.get(ActionProposal, proposal.id)).status == "expired"


async def test_modes_off_and_suggest(session_factory):
    off = {**DEFAULT_MODES, ActionKind.TASK_CREATE: Mode.OFF}
    async with session_factory() as session:
        with pytest.raises(ProposalError):
            await ProposalService(session, modes=off).propose(
                ActionKind.TASK_CREATE, CreateTask(title="x"), reason="r"
            )
    proposal, _ = await propose(session_factory)
    suggest = {**DEFAULT_MODES, ActionKind.TASK_MARK_DONE: Mode.SUGGEST}
    async with session_factory() as session:
        with pytest.raises(ProposalError):
            await ProposalService(session, modes=suggest).approve(proposal.id)


async def test_audit_log_rejects_updates_and_deletes(session_factory):
    await propose(session_factory)
    for statement in ("UPDATE audit_log SET event = 'x'", "DELETE FROM audit_log"):
        async with session_factory() as session:
            with pytest.raises(DBAPIError, match="append-only"):
                await session.execute(text(statement))
