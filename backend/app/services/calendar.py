"""Calendar rules: Monday–Sunday weeks and calendar months in the owner's timezone.

Rule R8: the Due date decides which week and month a task belongs to. Habit rows are titled
`dd/mm/yyyy`; older rows typed by hand use `d/m/yy`, so parsing accepts both.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

DEFAULT_TZ = ZoneInfo("Asia/Kolkata")

_DMY = re.compile(r"^\s*(\d{1,2})/(\d{1,2})/(\d{2}|\d{4})\s*$")
_ISO = re.compile(r"^\s*(\d{4})-(\d{2})-(\d{2})\s*$")


def local_date(value: date | datetime, tz: ZoneInfo = DEFAULT_TZ) -> date:
    """A date-only value is already local; a timestamp is converted to the owner's timezone."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.date()
        return value.astimezone(tz).date()
    return value


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def week_range(day: date) -> tuple[date, date]:
    start = week_start(day)
    return start, start + timedelta(days=6)


def week_key(day: date) -> str:
    iso_year, iso_week, _ = day.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def month_key(day: date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def month_range(day: date) -> tuple[date, date]:
    start = day.replace(day=1)
    next_month = (start + timedelta(days=32)).replace(day=1)
    return start, next_month - timedelta(days=1)


def format_habit_title(day: date) -> str:
    return f"{day.day:02d}/{day.month:02d}/{day.year:04d}"


def parse_habit_title(title: str) -> date | None:
    """Parse `dd/mm/yyyy`, legacy `d/m/yy`, or ISO `yyyy-mm-dd`. Returns None if unparseable."""
    try:
        if match := _DMY.match(title):
            day, month, year = (int(part) for part in match.groups())
            if year < 100:
                year += 2000
            return date(year, month, day)
        if match := _ISO.match(title):
            year, month, day = (int(part) for part in match.groups())
            return date(year, month, day)
    except ValueError:
        return None
    return None
