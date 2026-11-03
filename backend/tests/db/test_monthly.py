"""The monthly review: last month's tasks, scorecard, goals and weekly reviews, a drafted review,
the owner's note, and the month's page closed with next month's created."""

import uuid
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from app.llm.mock import MockLLM
from app.workflows.monthly import month_to_review
from app.workflows.schedule import Schedule, due_routines
from tests.fake_notion import FakeNotion
from tests.workflow_support import SPECS, make_runner, read, seed_period, seed_task

NOW = datetime(2026, 11, 1, 5, 0, tzinfo=UTC)  # 1 November, 10:30 in Asia/Kolkata


def workspace() -> tuple[FakeNotion, dict[str, str]]:
    fake = FakeNotion()
    ids = {
        "shipped": seed_task(fake, "Ship the API", status="Done", due=date(2026, 10, 8)),
        "docs": seed_task(fake, "Write the docs", status="Done", due=date(2026, 10, 22)),
        "slipped": seed_task(fake, "Migrate billing", status="Next", due=date(2026, 10, 27)),
        "october": seed_period(fake, "months", "Oct 2026", date(2026, 10, 1), date(2026, 10, 31)),
    }
    seed_period(fake, "weeks", "19 – 25 Oct", date(2026, 10, 19), date(2026, 10, 25),
                review="Good week: shipped the docs; billing slipped again.")  # fmt: skip
    fake.seed(SPECS["goals"], {"title": "Launch v1", "status": "Active", "current": 3,
                               "target": 10})  # fmt: skip
    fake.seed(SPECS["habits"], {"title": "05/10/2026", "dsa": True, "sleep": 7})
    return fake, ids


def months(fake: FakeNotion) -> list[dict]:
    spec = SPECS["months"]
    return [p for p in fake.pages.values() if p["parent"]["data_source_id"] == spec.data_source_id]


def test_the_first_week_of_a_month_reviews_the_month_before():
    assert month_to_review(date(2026, 11, 1)) == (date(2026, 10, 1), date(2026, 10, 31))
    assert month_to_review(date(2026, 11, 7)) == (date(2026, 10, 1), date(2026, 10, 31))
    assert month_to_review(date(2026, 11, 8)) == (date(2026, 11, 1), date(2026, 11, 30))
    assert month_to_review(date(2026, 11, 20), "2026-09-15") == (date(2026, 9, 1),
                                                                 date(2026, 9, 30))  # fmt: skip


def test_it_is_scheduled_on_the_first_at_the_review_time():
    schedule = Schedule(ZoneInfo("Asia/Kolkata"), time(7, 30), time(21, 0), 6, time(18, 0))
    first = due_routines(schedule, datetime(2026, 12, 1, 12, 30, tzinfo=UTC))  # 18:00 IST
    assert [d.job for d in first] == ["monthly_review"]
    assert due_routines(schedule, datetime(2026, 12, 2, 12, 30, tzinfo=UTC)) == []


async def test_a_month_is_reviewed_closed_and_the_next_one_prepared(session_factory):
    fake, ids = workspace()
    mock = MockLLM()
    mock.queue("MonthlyReviewDraft", {
        "summary": "Two of three planned tasks done; T1 slipped twice.",
        "wins": ["Shipped the API"], "misses": ["Billing migration"],
        "patterns": ["Docs landed once the API was done"],
        "focus": ["Finish billing", "Launch prep", "Rest", "Too many"],
        "goal_notes": [{"ref": "G1", "note": "3 of 10 milestones."}],
    })  # fmt: skip
    runner, _ = make_runner(session_factory, fake, mock, now=NOW)
    view = await runner.start("monthly_review")
    request = view.waiting_for
    assert view.status == "waiting_input" and request["type"] == "month_review"
    assert request["month"]["label"] == "Oct 2026" and request["closes_page"] is True
    assert request["metrics"]["planned"] == 3 and request["metrics"]["done"] == 2
    review = request["review"]
    assert review["summary"] == "Two of three planned tasks done; “Migrate billing” slipped twice."
    assert review["focus"] == ["Finish billing", "Launch prep", "Rest"]  # at most three
    assert review["goals"] == [{"title": "Launch v1", "note": "3 of 10 milestones."}]
    prompt = mock.calls[0]["prompt"]
    assert "billing slipped again" in prompt and "progress 3 of 10" in prompt

    view = await runner.resume(uuid.UUID(view.id), {"note": "Billing first in November."})
    kinds = sorted(c["kind"] for c in view.waiting_for["proposals"])
    assert kinds == ["month.close", "month.create"]
    view = await runner.resume(uuid.UUID(view.id), {"approve": "all"})
    assert view.status == "completed" and view.summary["execution"] == {"verified": 2}
    october = read(fake, "months", ids["october"])
    assert october["status"] == "Closed"
    assert october["review"].startswith("LifeOS monthly review · Oct 2026")
    assert "Launch v1: 3 of 10 milestones." in october["review"]
    assert october["review"].endswith("Owner's note: Billing first in November.")
    assert {read(fake, "months", p["id"])["title"] for p in months(fake)} == {
        "Oct 2026",
        "Nov 2026",
    }
    # Tasks and goals are never changed by the monthly review.
    assert read(fake, "tasks", ids["slipped"])["status"] == "Next"


async def test_without_a_model_the_review_comes_from_the_numbers(session_factory):
    fake, _ = workspace()
    runner, _ = make_runner(session_factory, fake, None, now=NOW)
    view = await runner.start("monthly_review")
    review = view.waiting_for["review"]
    assert review["source"] == "rules" and review["summary"].endswith("AI summary unavailable.")
    assert review["wins"] == ["Ship the API", "Write the docs"]
    assert review["misses"] == ["Migrate billing"]
