from datetime import UTC, date, datetime

import pytest

from app.services.calendar import (
    format_habit_title,
    local_date,
    month_key,
    month_range,
    parse_habit_title,
    week_key,
    week_range,
    week_start,
)


def test_weeks_run_monday_to_sunday_like_weekly_progress():
    # The owner's Weekly Progress page "26 Oct – 1 Nov" is Monday to Sunday.
    assert week_range(date(2026, 11, 1)) == (date(2026, 10, 26), date(2026, 11, 1))
    assert week_start(date(2026, 10, 26)) == date(2026, 10, 26)
    assert week_key(date(2026, 10, 26)) == week_key(date(2026, 11, 1))
    assert week_key(date(2026, 11, 1)) != week_key(date(2026, 11, 2))


def test_week_key_uses_iso_year_at_year_boundaries():
    # 2026 starts on a Thursday, so it has 53 ISO weeks.
    assert week_key(date(2026, 12, 31)) == "2026-W53"
    assert week_key(date(2027, 1, 1)) == "2026-W53"
    assert week_key(date(2027, 1, 4)) == "2027-W01"
    assert week_key(date(2026, 1, 1)) == "2026-W01"


def test_month_key_and_range():
    assert month_key(date(2026, 11, 1)) == "2026-11"
    assert month_range(date(2026, 2, 15)) == (date(2026, 2, 1), date(2026, 2, 28))
    assert month_range(date(2028, 2, 10)) == (date(2028, 2, 1), date(2028, 2, 29))
    assert month_range(date(2026, 12, 31)) == (date(2026, 12, 1), date(2026, 12, 31))


def test_timestamps_are_converted_to_india_time():
    # 20:00 UTC on 1 Nov is 01:30 on 2 Nov in Asia/Kolkata.
    assert local_date(datetime(2026, 11, 1, 20, 0, tzinfo=UTC)) == date(2026, 11, 2)
    assert local_date(date(2026, 11, 1)) == date(2026, 11, 1)


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("01/11/2026", date(2026, 11, 1)),  # the agreed format
        ("1/11/26", date(2026, 11, 1)),  # legacy, typed by hand
        ("28/10/26", date(2026, 10, 28)),
        (" 29/10/26 ", date(2026, 10, 29)),
        ("2026-10-27", date(2026, 10, 27)),  # the old documented format
        ("31/02/2026", None),
        ("Monday", None),
        ("", None),
    ],
)
def test_parse_habit_title(title, expected):
    assert parse_habit_title(title) == expected


def test_habit_titles_follow_the_workspace_format():
    assert format_habit_title(date(2026, 11, 1)) == "2026-11-01"  # ISO unless told otherwise
    assert format_habit_title(date(2026, 11, 1), "%d/%m/%Y") == "01/11/2026"
    for fmt in (None, "%d/%m/%Y", "%B %d, %Y"):
        title = format_habit_title(date(2027, 3, 9), fmt)
        assert parse_habit_title(title, fmt) == date(2027, 3, 9)
