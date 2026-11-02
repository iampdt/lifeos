import uuid
from datetime import date

import pytest
from pydantic import ValidationError

from app.execution.actions import (
    ActionKind,
    ClosePeriod,
    CreatePeriod,
    CreateTask,
    MarkDone,
    SetDue,
    SetStatus,
    UpdateBookProgress,
    UpsertHabitDay,
    WritePlan,
    build_plan,
    importance,
    normalize,
    read_fields,
)
from app.notion.codecs import RelationValue
from app.permissions.policy import PermissionDenied, check_plan
from tests.factories import specs, task_page

SPECS = specs()


def test_set_due_links_are_optional_but_never_both_kinds():
    """Workspaces without weekly/monthly pages just move the date."""
    plain = SetDue(page_id="t", new_due=date(2026, 11, 3), decision="carry_forward")
    plan = build_plan(ActionKind.TASK_SET_DUE, plain, SPECS)
    assert plan.values == {"due": date(2026, 11, 3)}
    with pytest.raises(ValidationError):  # both a page and a proposal for the same period
        SetDue(
            page_id="t", new_due=date(2026, 11, 3), decision="carry_forward",
            week_page_id="w", week_proposal_id=uuid.uuid4(), month_page_id="m",
        )  # fmt: skip


def test_payloads_reject_unknown_fields_and_bad_values():
    with pytest.raises(ValidationError):
        MarkDone(page_id="t", completed_on=date(2026, 11, 1), delete=True)
    with pytest.raises(ValidationError):
        UpsertHabitDay(day=date(2026, 11, 1), metrics={"sleep": -1})
    with pytest.raises(ValueError, match="not habits or metrics"):  # unknown in this workspace
        build_plan(
            ActionKind.HABIT_UPSERT_DAY,
            UpsertHabitDay(day=date(2026, 11, 1), checks={"gaming": True}),
            SPECS,
        )
    with pytest.raises(ValidationError):
        CreatePeriod(title="W", start=date(2026, 11, 8), end=date(2026, 11, 2))
    with pytest.raises(ValidationError):
        CreateTask(title="")


def test_plans_resolve_semantic_values_to_notion_options():
    done = build_plan(
        ActionKind.TASK_MARK_DONE, MarkDone(page_id="t", completed_on=date(2026, 11, 1)), SPECS
    )
    assert done == WritePlan("tasks", "t", {"status": "Done", "completed": date(2026, 11, 1)})
    cancel = build_plan(
        ActionKind.TASK_SET_STATUS, SetStatus(page_id="t", status="cancelled"), SPECS
    )
    assert cancel.values == {"status": "Cancelled"}
    assert (
        importance(ActionKind.TASK_SET_STATUS, SetStatus(page_id="t", status="cancelled"))
        == "important"
    )


def test_carry_forward_uses_pages_created_by_dependencies():
    week_proposal = uuid.uuid4()
    payload = SetDue(
        page_id="t", new_due=date(2026, 11, 3), decision="carry_forward",
        week_proposal_id=week_proposal, month_page_id="month-1",
    )  # fmt: skip
    plan = build_plan(ActionKind.TASK_SET_DUE, payload, SPECS, {week_proposal: "week-new"})
    assert plan.values == {"due": date(2026, 11, 3), "week": ["week-new"], "month": ["month-1"]}


def test_habit_and_period_plans_are_find_or_create():
    habit = build_plan(
        ActionKind.HABIT_UPSERT_DAY,
        UpsertHabitDay(day=date(2026, 11, 1), checks={"reading": True}, sleep=7),
        SPECS,
    )
    assert habit.page_id is None and habit.upsert and habit.lookup.day == date(2026, 11, 1)
    assert habit.values == {"title": "01/11/2026", "reading": True, "sleep": 7}
    week = build_plan(
        ActionKind.WEEK_CREATE,
        CreatePeriod(title="2 – 8 Nov", start=date(2026, 11, 2), end=date(2026, 11, 8)),
        SPECS,
    )
    assert week.data_source == "weeks" and not week.upsert and week.values["status"] == "Upcoming"


def test_ids_compare_regardless_of_dashes_and_case():
    assert normalize(["ABC-def"]) == normalize(RelationValue(ids=("abcdef",)))
    assert normalize(7.0) == 7 and normalize(date(2026, 11, 1)) == "2026-11-01"


def test_read_fields_returns_comparable_values():
    values = read_fields(
        SPECS["tasks"], task_page(weeks=("W-1",)), ["status", "due", "week", "goal"]
    )
    assert values == {"status": "Next", "due": "2026-11-03", "week": ["w1"], "goal": ["goal1"]}


def test_allowlist_refuses_fields_outside_it():
    tasks, goals, habits = SPECS["tasks"], SPECS["goals"], SPECS["habits"]
    check_plan(WritePlan("tasks", "t", {"status": "Done", "due": date(2026, 1, 1)}), tasks)
    with pytest.raises(PermissionDenied):
        check_plan(WritePlan("tasks", "t", {"career_evidence": ["x"]}), tasks)
    with pytest.raises(PermissionDenied):
        check_plan(WritePlan("goals", "g", {"status": "Done"}), goals)
    with pytest.raises(PermissionDenied):
        check_plan(WritePlan("feedback", "f", {"title": "x"}), tasks)
    # Habits allow exactly the workspace's checkboxes and numbers, plus title, day and notes.
    check_plan(WritePlan("habits", None, {"title": "x", "dsa": True, "sleep": 7}), habits)
    with pytest.raises(PermissionDenied):
        check_plan(WritePlan("habits", None, {"gaming": True}), habits)


def test_close_period_writes_review_and_closed_status():
    plan = build_plan(ActionKind.WEEK_CLOSE, ClosePeriod(page_id="w", review="Good week"), SPECS)
    assert plan == WritePlan("weeks", "w", {"review": "Good week", "status": "Closed"})
    check_plan(plan, SPECS["weeks"])
    month = build_plan(ActionKind.MONTH_CLOSE, ClosePeriod(page_id="m", review="x"), SPECS)
    assert month.data_source == "months"


def test_book_progress_plan_and_validation():
    payload = UpdateBookProgress(page_id="b", pages_read=120, status="reading")
    plan = build_plan(ActionKind.BOOK_UPDATE_PROGRESS, payload, SPECS)
    assert plan.values == {"status": "Reading", "pages_read": 120}
    check_plan(plan, SPECS["books"])
    with pytest.raises(ValidationError):
        UpdateBookProgress(page_id="b")
    with pytest.raises(ValidationError):
        UpdateBookProgress(page_id="b", pages_read=-1)
