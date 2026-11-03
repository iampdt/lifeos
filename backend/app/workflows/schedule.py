"""When each account's routines are due (multi-user §Scheduling).

Routines run in the account's own timezone: the morning briefing and the evening check-in every
day, the weekly review on its weekday, and the monthly review on the 1st (for the month just
ended) at the weekly review's time. A routine is due from its time until four hours later,
within the same local day, and runs at most once per account, routine and local date
(`cron_runs`). The scheduled tick comes every hour, so the window also covers a late or
missed tick.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

WINDOW = timedelta(hours=4)


def parse_time(text: str) -> time:
    hours, minutes = text.split(":")
    return time(int(hours), int(minutes))


@dataclass(frozen=True)
class Schedule:
    tz: ZoneInfo
    morning: time
    evening: time
    review_weekday: int  # Monday is 0
    review: time
    enabled: bool = True


@dataclass(frozen=True)
class Due:
    job: str
    local_date: date
    since: datetime  # when it became due, in UTC


def due_routines(schedule: Schedule, now: datetime) -> list[Due]:
    """The routines due at `now`, earliest first."""
    if not schedule.enabled:
        return []
    local = now.astimezone(schedule.tz)
    today = local.date()
    routines = [("morning_briefing", schedule.morning), ("evening_checkin", schedule.evening)]
    if today.weekday() == schedule.review_weekday:
        routines.append(("weekly_review", schedule.review))
    if today.day == 1:
        routines.append(("monthly_review", schedule.review))
    midnight = datetime.combine(today + timedelta(days=1), time(0), tzinfo=schedule.tz)
    due = []
    for job, at in routines:
        start = datetime.combine(today, at, tzinfo=schedule.tz)
        if start <= local < min(start + WINDOW, midnight):
            due.append(Due(job, today, start.astimezone(UTC)))
    return sorted(due, key=lambda d: d.since)
