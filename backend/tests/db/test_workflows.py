"""End-to-end workflow runs: FakeNotion + MockLLM + InMemorySaver + the test database."""

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from app.db.models import ActionProposal, AgentRun
from app.execution.executor import Executor
from app.llm.base import LLMUnavailable
from app.llm.mock import MockLLM
from app.workflows.runner import RunStateError
from tests.fake_notion import FakeNotion
from tests.workflow_support import (
    SPECS,
    make_runner,
    pages_in,
    read,
    seed_period,
    seed_task,
)

SUNDAY_EVENING = datetime(2026, 11, 1, 15, 0, tzinfo=UTC)  # 20:30 in Asia/Kolkata
MONDAY_MORNING = datetime(2026, 11, 2, 3, 0, tzinfo=UTC)  # 08:30


def evening_workspace() -> tuple[FakeNotion, dict[str, str]]:
    fake = FakeNotion()
    seed_period(fake, "weeks", "26 Oct – 1 Nov", date(2026, 10, 26), date(2026, 11, 1))
    seed_period(fake, "months", "OCT", date(2026, 10, 1), date(2026, 10, 31))
    ids = {
        "late": seed_task(fake, "Old overdue", priority="P0", due=date(2026, 10, 28)),
        "tests": seed_task(fake, "Write tests", priority="P1", due=date(2026, 11, 1)),
        "ddia": seed_task(fake, "Read DDIA ch 3", priority="P2", due=date(2026, 11, 1)),
        "backlog": seed_task(fake, "Undated backlog", priority="P1"),
        "later": seed_task(fake, "Later task", priority="P2", due=date(2026, 11, 5)),
        "standup": seed_task(fake, "Daily standup", due=date(2026, 11, 2), recurring=True),
    }
    ids["book"] = fake.seed(
        SPECS["books"],
        {"title": "Clean Code", "status": "Reading", "pages": 400, "pages_read": 100},
    )
    return fake, ids


def cards_by_kind(view) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for card in view.waiting_for["proposals"]:
        out.setdefault(card["kind"], []).append(card)
    return out


async def test_evening_checkin_full_loop(session_factory):
    fake, ids = evening_workspace()
    mock = MockLLM()
    mock.queue(
        "CheckinParse",
        {
            "done_refs": ["T2"],
            "partial_refs": ["T3"],
            "habits": {"dsa": True, "workout": False},
            "sleep_hours": 7,
            "screen_time_hours": 3.5,
            "pages_read": [{"ref": "B1", "pages": 20}],
            "learned": "LSM trees trade read cost for write throughput",
            "new_tasks": ["Book dentist"],
        },
    )
    mock.queue("DayPlan", {"picks": [{"ref": "T1", "reason": "Unblocks the week"}], "note": "x"})
    runner, _ = make_runner(session_factory, fake, mock, now=SUNDAY_EVENING)

    view = await runner.start("evening_checkin")
    assert view.status == "waiting_input"
    question = view.waiting_for
    assert question["type"] == "question"
    assert [t["title"] for t in question["tasks"]] == [
        "Old overdue",
        "Write tests",
        "Read DDIA ch 3",
    ]
    assert [t["ref"] for t in question["tasks"]] == ["T1", "T2", "T3"]

    view = await runner.resume(uuid_of(view), {"text": "Finished the tests, half of DDIA..."})
    assert view.status == "waiting_approval"
    cards = cards_by_kind(view)
    assert sorted(cards) == sorted([
        "task.mark_done", "habit.upsert_day", "book.update_progress", "task.create",
        "week.create", "month.create", "task.set_due",
    ])  # fmt: skip
    assert cards["task.mark_done"][0]["target"]["title"] == "Write tests"
    move = cards["task.set_due"][0]
    assert move["target"]["page_id"] == ids["backlog"] and len(move["depends_on"]) == 2
    # The model saw owner data only inside the marked data block.
    assert all("<notion_data>" in call["prompt"] for call in mock.calls)

    view = await runner.resume(uuid_of(view), {"approve": "all"})
    assert view.status == "completed"
    summary = view.summary
    assert summary["done"] == ["Write tests"] and summary["partial"] == ["Read DDIA ch 3"]
    assert summary["execution"] == {"verified": 7}
    assert summary["tomorrow"]["proposed"] == ["Undated backlog"]

    assert read(fake, "tasks", ids["tests"])["status"] == "Done"
    assert read(fake, "tasks", ids["tests"])["completed"] == date(2026, 11, 1)
    assert read(fake, "tasks", ids["ddia"])["due"] == date(2026, 11, 1)  # R6: not moved
    assert read(fake, "tasks", ids["late"])["due"] == date(2026, 10, 28)  # R6: not moved
    [habit] = pages_in(fake, "habits").values()
    assert habit["title"] == "01/11/2026" and habit["dsa"] is True and habit["workout"] is False
    assert habit["sleep"] == 7 and habit["screen_time"] == 3.5 and "LSM" in habit["notes"]
    assert read(fake, "books", ids["book"])["pages_read"] == 120
    inbox = [p for p in pages_in(fake, "tasks").values() if p["title"] == "Book dentist"]
    assert inbox and inbox[0]["status"] == "Inbox"
    new_week = next(
        i for i, p in pages_in(fake, "weeks").items() if p["start"] == date(2026, 11, 2)
    )
    new_month = next(i for i, p in pages_in(fake, "months").items() if p["title"] == "Nov 2026")
    assert read(fake, "weeks", new_week)["title"] == "2 Nov – 8 Nov"
    assert read(fake, "weeks", new_week)["status"] == "Upcoming"
    moved = read(fake, "tasks", ids["backlog"])
    assert moved["due"] == date(2026, 11, 2)
    assert moved["week"] == [new_week] and moved["month"] == [new_month]

    async with session_factory() as session:
        run = await session.get(AgentRun, uuid_of(view))
    assert run.status == "completed" and run.model == "mock-fast" and run.input_tokens > 0
    assert run.output["llm_calls"] == 2


async def test_evening_without_llm_falls_back_and_rejects_unapproved(session_factory):
    fake, ids = evening_workspace()
    mock = MockLLM()
    mock.queue("CheckinParse", LLMUnavailable("down"))
    mock.queue("DayPlan", LLMUnavailable("down"))
    runner, _ = make_runner(session_factory, fake, mock, now=SUNDAY_EVENING)

    view = await runner.start("evening_checkin")
    view = await runner.resume(uuid_of(view), {"text": "I finished write tests today"})
    cards = cards_by_kind(view)
    [done] = cards["task.mark_done"]
    assert done["target"]["page_id"] == ids["tests"]  # matched by title
    moves = {c["target"]["page_id"] for c in cards["task.set_due"]}
    assert moves == {ids["backlog"], ids["later"]}  # rule-based: P1 before P2

    view = await runner.resume(uuid_of(view), {"approve": [done["id"]]})
    assert view.status == "completed"
    assert view.summary["execution"] == {"verified": 1}
    assert any("AI unavailable" in w for w in view.summary["warnings"])
    assert read(fake, "tasks", ids["backlog"])["due"] is None
    async with session_factory() as session:
        statuses = set(await session.scalars(select(ActionProposal.status)))
    assert statuses == {"executed", "rejected"}


async def test_structured_answer_skips_the_model(session_factory):
    fake, ids = evening_workspace()
    mock = MockLLM()  # nothing queued: any model call would fail the run
    runner, _ = make_runner(session_factory, fake, mock, now=SUNDAY_EVENING)
    view = await runner.start("evening_checkin", inputs={"plan_tomorrow": False})
    view = await runner.resume(uuid_of(view), {"done_refs": ["T2", "T9"]})
    assert view.status == "waiting_approval"
    assert [c["kind"] for c in view.waiting_for["proposals"]] == ["task.mark_done"]
    assert mock.calls == []
    view = await runner.resume(uuid_of(view), {"approve": "all"})
    assert any("T9" in w for w in view.summary["warnings"])
    with pytest.raises(RunStateError):
        await runner.resume(uuid_of(view), {"approve": "all"})


async def test_writes_disabled_blocks_and_old_approvals_expire(session_factory):
    fake, ids = evening_workspace()
    runner, deps = make_runner(session_factory, fake, MockLLM(), now=SUNDAY_EVENING, writes=False)
    view = await runner.start("evening_checkin", inputs={"plan_tomorrow": False})
    view = await runner.resume(uuid_of(view), {"done_refs": ["T2"]})
    view = await runner.resume(uuid_of(view), {"approve": "all"})
    assert view.summary["execution"] == {"blocked": 1}
    assert any("Writes are disabled" in n for n in view.summary["notes"])
    assert read(fake, "tasks", ids["tests"])["status"] == "Next"

    # Turning writes on two days later must not execute the forgotten approval.
    later = Executor(session_factory, fake, SPECS, writes_enabled=True,
                     clock=lambda: SUNDAY_EVENING + timedelta(days=2))  # fmt: skip
    [result] = await later.execute_approved()
    assert result.outcome == "skipped" and result.detail == "approval expired"
    assert read(fake, "tasks", ids["tests"])["status"] == "Next"


async def test_morning_trims_overloaded_day(session_factory):
    fake = FakeNotion()
    week = seed_period(fake, "weeks", "2 Nov – 8 Nov", date(2026, 11, 2), date(2026, 11, 8))
    month = seed_period(fake, "months", "Nov 2026", date(2026, 11, 1), date(2026, 11, 30))
    today, tuesday = date(2026, 11, 2), date(2026, 11, 3)
    for title, priority in [("M0", "P0"), ("M1a", "P1"), ("M1b", "P1"), ("M2", "P2"), ("M3", "P3")]:
        seed_task(fake, title, priority=priority, due=today)
    for title in ("X1", "X2", "X3"):
        seed_task(fake, title, priority="P2", due=tuesday)
    seed_task(fake, "Late", priority="P1", due=date(2026, 10, 30))
    mock = MockLLM()
    mock.queue("DayPlan", {"picks": [{"ref": r, "reason": "keep"} for r in ("T1", "T2", "T3")]})
    runner, _ = make_runner(session_factory, fake, mock, now=MONDAY_MORNING)

    view = await runner.start("morning_briefing")
    assert view.status == "waiting_approval"
    cards = view.waiting_for["proposals"]
    assert {c["target"]["title"] for c in cards} == {"M2", "M3"}
    assert {c["payload"]["new_due"] for c in cards} == {"2026-11-04"}  # Tuesday is full (R1)
    assert all(c["payload"]["week_page_id"] == week for c in cards)

    view = await runner.resume(uuid_of(view), {"approve": "all"})
    summary = view.summary
    assert summary["priorities"] == ["M0", "M1a", "M1b"]
    assert summary["execution"] == {"verified": 2}
    assert "1 overdue tasks wait for Sunday's review (R6)." in summary["observations"]
    assert "No scorecard for 30 Oct, 31 Oct, 01 Nov." in summary["observations"]
    moved = [p for p in pages_in(fake, "tasks").values() if p["title"] in ("M2", "M3")]
    assert all(p["due"] == date(2026, 11, 4) and p["week"] == [week] and p["month"] == [month]
               for p in moved)  # fmt: skip


async def test_morning_with_balanced_day_proposes_nothing(session_factory):
    fake = FakeNotion()
    seed_task(fake, "Only one", priority="P1", due=date(2026, 11, 2))
    runner, _ = make_runner(session_factory, fake, MockLLM(), now=MONDAY_MORNING)
    view = await runner.start("morning_briefing")
    assert view.status == "completed"
    assert view.summary["priorities"] == ["Only one"] and view.summary["execution"] == {}


async def test_weekly_review_decisions_close_and_carry(session_factory):
    fake = FakeNotion()
    this_week = seed_period(
        fake, "weeks", "26 Oct – 1 Nov", date(2026, 10, 26), date(2026, 11, 1),
        review="My own notes",
    )  # fmt: skip
    seed_period(fake, "months", "OCT", date(2026, 10, 1), date(2026, 10, 31))
    seed_task(fake, "Shipped feature", status="Done", due=date(2026, 10, 28), area="Career Switch")
    late = seed_task(fake, "Old overdue", priority="P0", due=date(2026, 10, 28))
    stale = seed_task(fake, "Stale idea", priority="P3", due=date(2026, 10, 30))
    seed_task(fake, "Cancelled thing", status="Cancelled", due=date(2026, 10, 29))
    inbox = seed_task(fake, "Inbox item", status="Inbox")
    mock = MockLLM()
    mock.queue(
        "WeeklyReviewDraft",
        {
            "summary": "One feature shipped; two tasks slipped.",
            "wins": ["Shipped feature"],
            "misses": ["Old overdue"],
            "priorities": ["Interview prep"],
            "advice": [
                {"ref": "T1", "decision": "carry_forward", "reason": "Still important"},
                {"ref": "T2", "decision": "cancel", "reason": "Postponed, low priority"},
            ],
        },
    )
    runner, _ = make_runner(session_factory, fake, mock, now=SUNDAY_EVENING)

    view = await runner.start("weekly_review")
    assert view.status == "waiting_input"
    request = view.waiting_for
    assert request["type"] == "decisions"
    assert request["metrics"]["planned"] == 3 and request["metrics"]["done"] == 1
    assert request["metrics"]["cancelled"] == 1
    assert [(r["ref"], r["recommended"]) for r in request["overdue"]] == [
        ("T1", "carry_forward"), ("T2", "cancel"),
    ]  # fmt: skip
    assert request["overdue"][0]["suggested_due"] == "2026-11-02"

    view = await runner.resume(
        uuid_of(view),
        {"overdue": {"T1": {"decision": "reschedule", "due": "2026-11-04"}},
         "inbox": {"I1": {"action": "next"}}, "note": "Good week overall"},
    )  # fmt: skip
    assert view.status == "waiting_approval"
    kinds = sorted(c["kind"] for c in view.waiting_for["proposals"])
    assert kinds == sorted([
        "week.create", "month.create", "task.set_due", "task.set_status", "task.set_status",
        "week.close",
    ])  # fmt: skip
    cancel = next(c for c in view.waiting_for["proposals"] if c["target"]["page_id"] == stale)
    assert cancel["importance"] == "important"

    view = await runner.resume(uuid_of(view), {"approve": "all"})
    assert view.summary["execution"] == {"verified": 6}
    assert view.summary["overdue_decisions"] == {"reschedule": 1, "cancel": 1}
    new_week = next(
        i for i, p in pages_in(fake, "weeks").items() if p["start"] == date(2026, 11, 2)
    )
    moved = read(fake, "tasks", late)
    assert moved["due"] == date(2026, 11, 4) and moved["week"] == [new_week]
    assert read(fake, "tasks", stale)["status"] == "Cancelled"
    assert read(fake, "tasks", inbox)["status"] == "Next"
    closed = read(fake, "weeks", this_week)
    assert closed["status"] == "Closed"
    assert closed["review"].startswith("My own notes\n\nLifeOS weekly review · 26 Oct – 1 Nov")
    assert "Owner's note: Good week overall" in closed["review"]


async def test_unknown_workflow(session_factory):
    runner, _ = make_runner(session_factory, FakeNotion(), MockLLM(), now=SUNDAY_EVENING)
    from app.workflows.runner import UnknownWorkflow

    with pytest.raises(UnknownWorkflow):
        await runner.start("monthly_magic")


def uuid_of(view):
    import uuid

    return uuid.UUID(view.id)
