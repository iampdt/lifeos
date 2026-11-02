"""Domain rules as pure functions (architecture §4). Rule IDs appear in proposals and logs.

Task status and priority are Notion option names ("Next", "P1"); `Vocabulary` maps them back
to the semantic keys in the mapping, so no rule hard-codes a display name.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from statistics import mean

from app.domain.models import HabitDay, Task
from app.notion.mapping import DataSourceSpec

MAX_MAJOR_PER_DAY = 3  # R1's default; each workspace can set its own limit
MAJOR_STATUSES = frozenset({"next", "doing"})
CLOSED_STATUSES = frozenset({"done", "cancelled"})


@dataclass(frozen=True)
class Vocabulary:
    status: dict[str, str]  # semantic key → Notion option name
    priority: dict[str, str]

    @classmethod
    def from_spec(cls, tasks: DataSourceSpec) -> Vocabulary:
        priority = tasks.properties.get("priority")
        # Priority keys p0, p1, … sort from highest to lowest, whatever the options are called.
        ranked = dict(sorted((priority.values if priority else {}).items()))
        return cls(dict(tasks.prop("status").values), ranked)

    def status_key(self, task: Task) -> str | None:
        for key, name in self.status.items():
            if name == task.status:
                return key
        return None

    def priority_rank(self, task: Task) -> int:
        """P0 → 0 … P3 → 3; no priority sorts last."""
        for rank, name in enumerate(self.priority.values()):
            if name == task.priority:
                return rank
        return len(self.priority)


def is_open(task: Task, vocab: Vocabulary) -> bool:
    """R2: active means Status is not Done or Cancelled (a blank Status counts as open)."""
    return vocab.status_key(task) not in CLOSED_STATUSES


def is_major(task: Task, day: date, vocab: Vocabulary) -> bool:
    """R1: Due that day, Status Next or Doing, not Recurring."""
    return task.due == day and vocab.status_key(task) in MAJOR_STATUSES and not task.recurring


def major_tasks(tasks: Iterable[Task], day: date, vocab: Vocabulary) -> list[Task]:
    return fallback_order([t for t in tasks if is_major(t, day, vocab)], vocab)


def free_slots(
    tasks: Iterable[Task], day: date, vocab: Vocabulary, limit: int = MAX_MAJOR_PER_DAY
) -> int:
    return max(0, limit - len(major_tasks(tasks, day, vocab)))


def fallback_order(tasks: Iterable[Task], vocab: Vocabulary) -> list[Task]:
    """Deterministic planning order (architecture §5.5): P0, then P1, …, then earliest Due."""
    return sorted(
        tasks, key=lambda t: (vocab.priority_rank(t), t.due or date.max, t.title.casefold())
    )


def overdue(tasks: Iterable[Task], today: date, vocab: Vocabulary) -> list[Task]:
    """R6: the carry-forward queue. Only the weekly review proposes decisions for these."""
    return fallback_order(
        [t for t in tasks if is_open(t, vocab) and t.due is not None and t.due < today], vocab
    )


def planning_candidates(
    tasks: Iterable[Task],
    day: date,
    vocab: Vocabulary,
    *,
    exclude_ids: Iterable[str] = (),
    horizon_days: int = 14,
    limit: int = 15,
) -> list[Task]:
    """Tasks that may be scheduled on `day`: Next or Doing, not Recurring, and either undated or
    due after `day` within the horizon (pulled forward). Overdue tasks are never candidates (R6),
    and tasks already due on `day` are already planned."""
    excluded = set(exclude_ids)
    latest = day + timedelta(days=horizon_days)
    eligible = [
        t
        for t in tasks
        if t.id not in excluded
        and vocab.status_key(t) in MAJOR_STATUSES
        and not t.recurring
        and (t.due is None or day < t.due <= latest)
    ]
    return fallback_order(eligible, vocab)[:limit]


def allocate_days(
    to_place: Sequence[Task],
    existing: Iterable[Task],
    start: date,
    end: date,
    vocab: Vocabulary,
    limit: int = MAX_MAJOR_PER_DAY,
) -> dict[str, date | None]:
    """Give each task (in the order given) the earliest day in [start, end] with a free
    major-task slot (R1). Tasks that can't fit get None. Tasks being placed don't count
    toward their old day."""
    moving = {t.id for t in to_place}
    load: Counter[date] = Counter(
        t.due
        for t in existing
        if t.id not in moving and t.due is not None and is_major(t, t.due, vocab)
    )
    placed: dict[str, date | None] = {}
    for task in to_place:
        counts_as_major = vocab.status_key(task) in MAJOR_STATUSES and not task.recurring
        day = start
        placed[task.id] = None
        while day <= end:
            if not counts_as_major or load[day] < limit:
                placed[task.id] = day
                if counts_as_major:
                    load[day] += 1
                break
            day += timedelta(days=1)
    return placed


# --- weekly metrics (R5) -------------------------------------------------------------------


@dataclass(frozen=True)
class AreaCount:
    planned: int = 0
    done: int = 0


@dataclass(frozen=True)
class WeekMetrics:
    planned: int
    done: int
    cancelled: int
    still_open: int
    by_area: dict[str, AreaCount] = field(default_factory=dict)

    @property
    def completion_rate(self) -> float | None:
        return self.done / self.planned if self.planned else None


def week_metrics(tasks_due_in_week: Iterable[Task], vocab: Vocabulary) -> WeekMetrics:
    """R5: planned = Due in the week and Status not Inbox (Cancelled reported separately);
    done = Status Done."""
    planned = done = cancelled = still_open = 0
    areas: dict[str, AreaCount] = {}
    for task in tasks_due_in_week:
        key = vocab.status_key(task)
        if key == "cancelled":
            cancelled += 1
            continue
        if key == "inbox" or key is None:
            continue
        planned += 1
        area = task.area or "No area"
        current = areas.get(area, AreaCount())
        if key == "done":
            done += 1
            areas[area] = AreaCount(current.planned + 1, current.done + 1)
        else:
            still_open += 1
            areas[area] = AreaCount(current.planned + 1, current.done)
    return WeekMetrics(planned, done, cancelled, still_open, dict(sorted(areas.items())))


@dataclass(frozen=True)
class HabitSummary:
    days_logged: int
    checks: dict[str, int]  # habit → days checked
    averages: dict[str, float] = field(default_factory=dict)  # metric → mean of logged values

    @classmethod
    def from_dict(cls, data: dict) -> HabitSummary:
        """From a run's saved state, including runs saved before metrics were generic."""
        averages = dict(data.get("averages") or {})
        for old, key in (("avg_sleep", "sleep"), ("avg_screen_time", "screen_time")):
            if data.get(old) is not None:
                averages.setdefault(key, data[old])
        return cls(data.get("days_logged", 0), dict(data.get("checks") or {}), averages)


def habit_summary(rows: Iterable[HabitDay], start: date, end: date) -> HabitSummary:
    in_range = [r for r in rows if r.day is not None and start <= r.day <= end]
    # If a day has duplicate rows, a habit counts once when any row has it checked.
    by_day: dict[date, dict[str, bool]] = {}
    for row in in_range:
        merged = by_day.setdefault(row.day, {})  # type: ignore[arg-type]
        for name, checked in row.checks.items():
            merged[name] = merged.get(name, False) or checked
    checks: Counter[str] = Counter()
    for day_checks in by_day.values():
        checks.update(name for name, checked in day_checks.items() if checked)
    values: dict[str, list[float]] = {}
    for row in in_range:
        for name, value in row.metrics.items():
            values.setdefault(name, []).append(value)
    return HabitSummary(
        days_logged=len(by_day),
        checks=dict(sorted(checks.items())),
        averages={name: round(mean(v), 1) for name, v in sorted(values.items())},
    )


def missing_scorecard_days(rows: Iterable[HabitDay], start: date, end: date) -> list[date]:
    logged = {r.day for r in rows}
    days = (start + timedelta(days=i) for i in range((end - start).days + 1))
    return [d for d in days if d not in logged]
