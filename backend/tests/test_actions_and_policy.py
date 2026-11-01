import uuid
from datetime import date

import pytest
from pydantic import ValidationError

from app.execution.actions import (
    ActionKind,
    CreatePeriod,
    CreateTask,
    MarkDone,
    SetDue,
    SetStatus,
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


def test_set_due_must_link_the_new_week_and_month():
    with pytest.raises(ValidationError):
        SetDue(page_id="t", new_due=date(2026, 11, 3), decision="carry_forward")
    with pytest.raises(ValidationError):  # both a page and a proposal for the same period
        SetDue(
            page_id="t", new_due=date(2026, 11, 3), decision="carry_forward",
            week_page_id="w", week_proposal_id=uuid.uuid4(), month_page_id="m",
        )  # fmt: skip


def test_payloads_reject_unknown_fields_and_bad_values():
    with pytest.raises(ValidationError):
        MarkDone(page_id="t", completed_on=date(2026, 11, 1), delete=True)
    with pytest.raises(ValidationError):
        UpsertHabitDay(day=date(2026, 11, 1), checks={"gaming": True})
    with pytest.raises(ValidationError):
        UpsertHabitDay(day=date(2026, 11, 1), sleep=30)
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
    check_plan(WritePlan("tasks", "t", {"status": "Done", "due": date(2026, 1, 1)}))
    with pytest.raises(PermissionDenied):
        check_plan(WritePlan("tasks", "t", {"career_evidence": ["x"]}))
    with pytest.raises(PermissionDenied):
        check_plan(WritePlan("goals", "g", {"status": "Done"}))
    with pytest.raises(PermissionDenied):
        check_plan(WritePlan("feedback", "f", {"title": "x"}))
