"""When routines are due: each account's own timezone, a four-hour window, the review's day."""

from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

from app.workflows.schedule import Schedule, due_routines, parse_time

IST = Schedule(ZoneInfo("Asia/Kolkata"), time(7, 30), time(21, 0), 6, time(18, 0))


def jobs(schedule: Schedule, utc: str) -> list[str]:
    return [d.job for d in due_routines(schedule, datetime.fromisoformat(utc).replace(tzinfo=UTC))]


def test_a_routine_is_due_for_four_hours_from_its_local_time():
    assert jobs(IST, "2026-11-03T01:59") == []  # 07:29 IST
    assert jobs(IST, "2026-11-03T02:00") == ["morning_briefing"]  # 07:30
    assert jobs(IST, "2026-11-03T05:59") == ["morning_briefing"]  # 11:29
    assert jobs(IST, "2026-11-03T06:00") == []  # 11:30


def test_the_window_ends_at_local_midnight():
    assert jobs(IST, "2026-11-03T18:29") == ["evening_checkin"]  # 23:59 IST
    assert jobs(IST, "2026-11-03T18:30") == []  # 00:00 the next day


def test_the_weekly_review_only_on_its_day():
    assert jobs(IST, "2026-11-08T12:30") == ["weekly_review"]  # Sunday 18:00 IST
    assert jobs(IST, "2026-11-07T12:30") == []  # Saturday
    both = jobs(IST, "2026-11-08T15:30")  # Sunday 21:00: the review is still in its window
    assert both == ["weekly_review", "evening_checkin"]  # oldest first


def test_each_account_uses_its_own_timezone_and_times():
    berlin = Schedule(ZoneInfo("Europe/Berlin"), parse_time("06:00"), parse_time("22:15"), 0,
                      parse_time("09:00"))  # fmt: skip
    assert jobs(berlin, "2026-11-03T05:00") == ["morning_briefing"]  # 06:00 CET
    # Monday 09:00 CET: the review, and the morning briefing still inside its window.
    assert jobs(berlin, "2026-11-02T08:00") == ["morning_briefing", "weekly_review"]
    due = due_routines(berlin, datetime(2026, 11, 3, 21, 20, tzinfo=UTC))
    assert [(d.job, d.local_date.isoformat()) for d in due] == [("evening_checkin", "2026-11-03")]


def test_disabled_schedules_run_nothing():
    off = Schedule(IST.tz, IST.morning, IST.evening, IST.review_weekday, IST.review, enabled=False)
    assert jobs(off, "2026-11-03T02:00") == []
