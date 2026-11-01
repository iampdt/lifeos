from datetime import date

from app.domain.models import HabitDay
from app.services.calendar import month_title, week_title
from app.services.rules import (
    Vocabulary,
    allocate_days,
    fallback_order,
    free_slots,
    habit_summary,
    is_major,
    is_open,
    missing_scorecard_days,
    overdue,
    planning_candidates,
    week_metrics,
)
from tests.workflow_support import SPECS, task

VOCAB = Vocabulary.from_spec(SPECS["tasks"])
MON, TUE = date(2026, 11, 2), date(2026, 11, 3)


def test_major_task_rule_r1():
    assert is_major(task("a", due=MON), MON, VOCAB)
    assert is_major(task("a", status="Doing", due=MON), MON, VOCAB)
    assert not is_major(task("a", status="Waiting", due=MON), MON, VOCAB)
    assert not is_major(task("a", due=MON, recurring=True), MON, VOCAB)
    assert not is_major(task("a", due=TUE), MON, VOCAB)


def test_open_and_free_slots():
    assert is_open(task("a", status=None), VOCAB)
    assert not is_open(task("a", status="Done"), VOCAB)
    tasks = [task(str(i), id=str(i), due=MON) for i in range(2)]
    assert free_slots(tasks, MON, VOCAB) == 1
    assert free_slots([*tasks, task("x", id="x", due=MON)], MON, VOCAB) == 0


def test_fallback_order_is_priority_then_due():
    tasks = [
        task("late p1", priority="P1", due=TUE),
        task("none"),
        task("p0", priority="P0", due=TUE),
        task("early p1", priority="P1", due=MON),
    ]
    assert [t.title for t in fallback_order(tasks, VOCAB)] == ["p0", "early p1", "late p1", "none"]


def test_candidates_exclude_overdue_due_today_recurring_and_waiting():
    tasks = [
        task("undated"),
        task("later", due=date(2026, 11, 6)),
        task("overdue", due=date(2026, 10, 30)),
        task("same day", due=MON),
        task("recurring", recurring=True),
        task("waiting", status="Waiting"),
        task("inbox", status="Inbox"),
        task("far", due=date(2026, 12, 30)),
    ]
    names = {t.title for t in planning_candidates(tasks, MON, VOCAB)}
    assert names == {"undated", "later"}


def test_overdue_queue():
    tasks = [
        task("late", due=date(2026, 10, 30)),
        task("done", status="Done", due=date(2026, 10, 1)),
    ]
    assert [t.title for t in overdue(tasks, MON, VOCAB)] == ["late"]


def test_allocate_days_respects_three_per_day():
    existing = [task(f"x{i}", id=f"x{i}", due=MON) for i in range(3)]
    moving = [task("a", id="a"), task("b", id="b"), task("w", id="w", status="Waiting")]
    placed = allocate_days(moving, existing, MON, TUE, VOCAB)
    assert placed == {"a": TUE, "b": TUE, "w": MON}
    full = allocate_days([task("c", id="c")], existing, MON, MON, VOCAB)
    assert full == {"c": None}


def test_week_metrics_r5():
    tasks = [
        task("d", status="Done", area="Work"),
        task("o", area="Work"),
        task("c", status="Cancelled"),
        task("i", status="Inbox"),
    ]
    m = week_metrics(tasks, VOCAB)
    assert (m.planned, m.done, m.cancelled, m.still_open) == (2, 1, 1, 1)
    assert m.completion_rate == 0.5 and m.by_area["Work"].planned == 2


def test_habit_summary_merges_duplicate_days():
    def row(day, **checks):
        return HabitDay(id=str(day) + str(checks), url="", title="", last_edited_time=MON,
                        day=day, checks=checks, sleep=7.0)  # fmt: skip

    rows = [row(MON, dsa=True), row(MON, dsa=False, workout=True), row(TUE, dsa=True)]
    summary = habit_summary(rows, MON, date(2026, 11, 8))
    assert summary.days_logged == 2 and summary.checks == {"dsa": 2, "workout": 1}
    assert summary.avg_sleep == 7.0 and summary.avg_screen_time is None
    assert missing_scorecard_days(rows, MON, date(2026, 11, 4)) == [date(2026, 11, 4)]


def test_period_titles():
    assert week_title(date(2026, 11, 4)) == "2 Nov – 8 Nov"
    assert week_title(date(2026, 12, 30)) == "28 Dec – 3 Jan"
    assert month_title(date(2026, 11, 4)) == "Nov 2026"
