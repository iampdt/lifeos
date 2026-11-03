"""Goals and reading in the weekly review: where each goal stands, a next step for a goal with
none, progress figures from the owner, and reading pace."""

import uuid
from datetime import UTC, date, datetime

from app.agents.goals import goal_health, reading_pace
from app.domain.models import Book, Goal, Task
from app.llm.mock import MockLLM
from tests.fake_notion import FakeNotion
from tests.workflow_support import SPECS, make_runner, pages_in, read, seed_task

SUNDAY_EVENING = datetime(2026, 11, 1, 15, 0, tzinfo=UTC)  # 20:30 in Asia/Kolkata
TODAY = date(2026, 11, 1)
EDITED = datetime(2026, 11, 1, tzinfo=UTC)


def goal(**values) -> Goal:
    return Goal(id="g", url="", title="Launch", last_edited_time=EDITED, **values)


def task(title: str, **values) -> Task:
    return Task(id=title, url="", title=title, last_edited_time=EDITED, **values)


def test_goal_health_from_numbers_and_tasks():
    work = [task("Write docs", goal_ids=("g",))]
    year = {"start": date(2026, 1, 1), "deadline": date(2026, 12, 31), "target": 10}
    assert goal_health(goal(current=10, target=10), [], [], TODAY).status == "done"
    assert goal_health(goal(current=1, **year), [], [], TODAY).status == "no_next_step"
    behind = goal_health(goal(current=3, **year), work, [], TODAY)
    assert behind.status == "behind"
    assert behind.facts == "30% done with 84% of the time gone (3 of 10). Deadline 31 Dec 2026."
    on_pace = goal_health(goal(current=8, **year), work, [], TODAY)
    assert on_pace.status == "on_pace" and on_pace.facts.startswith("80% done with 84%")
    assert goal_health(goal(), work, [], TODAY).status == "unmeasured"


def test_reading_pace_projects_a_finish_date():
    book = Book(id="b", url="", title="DDIA", last_edited_time=EDITED, pages=600,
                pages_read=200, started=date(2026, 10, 4))  # fmt: skip
    pace = reading_pace(book, TODAY)
    assert pace["pages_per_week"] == 50 and pace["finish"] == "2026-12-27"
    assert reading_pace(book.model_copy(update={"started": None}), TODAY)["finish"] is None


def workspace() -> tuple[FakeNotion, dict[str, str]]:
    fake = FakeNotion()
    launch = {"title": "Launch v1", "status": "Active", "current": 3, "target": 10}
    ids = {
        "launch": fake.seed(SPECS["goals"], launch),
        "fit": fake.seed(SPECS["goals"], {"title": "Run a 10k", "status": "Active"}),
    }
    ids["docs"] = seed_task(fake, "Write the docs", due=date(2026, 11, 5), goal=[ids["launch"]])
    fake.seed(SPECS["books"], {"title": "DDIA", "status": "Reading", "pages": 600,
                               "pages_read": 200, "started": date(2026, 10, 4)})  # fmt: skip
    return fake, ids


def review_draft() -> dict:
    return {"summary": "A quiet week.", "wins": [], "misses": [], "priorities": []}


async def test_goals_get_notes_a_next_step_and_reported_progress(session_factory):
    fake, ids = workspace()
    mock = MockLLM()
    mock.queue("WeeklyReviewDraft", review_draft())
    mock.queue("GoalAdvice", {"goals": [
        {"ref": "G1", "note": "Docs are the next milestone.", "next_step": "Ignored: has a task"},
        {"ref": "G2", "note": "No plan yet.", "next_step": "Book a 5k race for December."},
        {"ref": "G9", "note": "Unknown goal", "next_step": "Never shown"},
    ]})  # fmt: skip
    runner, _ = make_runner(session_factory, fake, mock, now=SUNDAY_EVENING)
    view = await runner.start("weekly_review")
    goals = {g["title"]: g for g in view.waiting_for["goals"]}
    assert goals["Launch v1"]["status"] == "on_pace" and goals["Launch v1"]["open_tasks"] == 1
    assert goals["Launch v1"]["facts"] == "30% done (3 of 10)."
    assert goals["Launch v1"]["next_step"] == "" and goals["Launch v1"]["can_set_current"]
    assert goals["Run a 10k"]["status"] == "no_next_step"
    assert goals["Run a 10k"]["next_step"] == "Book a 5k race for December"
    assert goals["Run a 10k"]["note"] == "No plan yet."
    [book] = view.waiting_for["reading"]
    assert book["title"] == "DDIA" and book["pages_per_week"] == 50
    prompt = next(c for c in mock.calls if c["schema"] == "GoalAdvice")["prompt"]
    assert "open tasks: Write the docs" in prompt

    launch_ref = goals["Launch v1"]["ref"]
    view = await runner.resume(uuid.UUID(view.id), {"goals": {launch_ref: {"current": "4"}}})
    cards = view.waiting_for["proposals"]
    progress = next(c for c in cards if c["kind"] == "goal.set_current")
    assert progress["payload"]["current"] == 4 and progress["before"] == {"current": 3}
    step = next(c for c in cards if c["kind"] == "task.create")
    assert step["payload"]["title"] == "Book a 5k race for December"
    view = await runner.resume(uuid.UUID(view.id), {"approve": "all"})
    assert view.summary["goals"] == {"progress_updates": 1, "next_steps": 1}
    assert read(fake, "goals", ids["launch"])["current"] == 4
    [created] = [t for t in pages_in(fake, "tasks").values()
                 if t["title"] == "Book a 5k race for December"]  # fmt: skip
    assert created["goal"] == [ids["fit"]] and created["status"] == "Inbox"


async def test_steps_can_be_declined_and_bad_numbers_are_ignored(session_factory):
    fake, ids = workspace()
    mock = MockLLM()
    mock.queue("WeeklyReviewDraft", review_draft())
    mock.queue("GoalAdvice", {"goals": [{"ref": "G2", "note": "-", "next_step": "Run 3 km"}]})
    runner, _ = make_runner(session_factory, fake, mock, now=SUNDAY_EVENING)
    view = await runner.start("weekly_review")
    refs = {g["title"]: g["ref"] for g in view.waiting_for["goals"]}
    view = await runner.resume(uuid.UUID(view.id), {"goals": {
        refs["Run a 10k"]: {"add_step": False},
        refs["Launch v1"]: {"current": "lots"},
    }})  # fmt: skip
    kinds = {c["kind"] for c in view.waiting_for["proposals"]}
    assert "goal.set_current" not in kinds and "task.create" not in kinds
    view = await runner.resume(uuid.UUID(view.id), {"approve": "all"})
    assert "G1: 'lots' isn't a number; progress unchanged." in view.summary["warnings"]


async def test_without_a_model_goals_show_their_facts_only(session_factory):
    fake, _ = workspace()
    runner, _ = make_runner(session_factory, fake, None, now=SUNDAY_EVENING)
    view = await runner.start("weekly_review")
    goals = {g["title"]: g for g in view.waiting_for["goals"]}
    assert goals["Run a 10k"]["facts"] == "No open task moves this goal forward."
    assert goals["Run a 10k"]["next_step"] == "" and goals["Run a 10k"]["note"] == ""
