"""Goal and learning specialists for the weekly review (architecture §5.3).

Goals: code works out where each active goal stands from its own numbers (progress against the
share of its time that has passed) and its linked tasks. The model adds a sentence per goal and,
for a goal with no open task, one concrete next task. A next task is proposed as an Inbox task
linked to the goal, so nothing changes without approval. Progress figures come only from the
owner (`goal.set_current`); the model never sets them.

Reading: pace and a projected finish date per book being read, from pages read since it was
started. That is arithmetic, so no model is involved.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.agents.common import ask_model, unavailable_warning
from app.domain.models import Book, Goal, Task
from app.llm.router import LLMRouter
from app.services.context import (
    DATA_RULES,
    MEMORY_RULES,
    RefMap,
    clean,
    data_block,
    memory_block,
)

GoalStatus = Literal["done", "behind", "no_next_step", "on_pace", "unmeasured"]
MAX_STEPS = 3
BEHIND_MARGIN = 0.1  # progress more than 10 points below the share of time gone


@dataclass
class GoalHealth:
    goal_id: str
    title: str
    status: GoalStatus
    facts: str  # one line from the numbers, shown whatever the model says
    progress: float | None = None
    time_gone: float | None = None
    open_tasks: list[str] = field(default_factory=list)  # titles
    done_this_week: list[str] = field(default_factory=list)
    current: float | None = None
    target: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _num(value: float | None) -> str:
    return "?" if value is None else f"{value:g}"


def goal_health(
    goal: Goal, open_tasks: Sequence[Task], done_this_week: Sequence[Task], today: date
) -> GoalHealth:
    progress = None
    if goal.current is not None and goal.target:
        progress = max(0.0, goal.current / goal.target)
    time_gone = None
    if goal.start and goal.deadline and goal.deadline > goal.start:
        span = (goal.deadline - goal.start).days
        time_gone = min(1.0, max(0.0, (today - goal.start).days / span))
    measured = f"{_num(goal.current)} of {_num(goal.target)}" if goal.target else ""
    if progress is not None and progress >= 1:
        status: GoalStatus = "done"
        facts = f"Target reached ({measured})."
    elif not open_tasks:
        status = "no_next_step"
        facts = "No open task moves this goal forward."
    elif progress is not None and time_gone is not None and progress + BEHIND_MARGIN < time_gone:
        status = "behind"
        facts = f"{progress:.0%} done with {time_gone:.0%} of the time gone ({measured})."
    elif progress is None:
        status = "unmeasured"
        facts = f"{len(open_tasks)} open tasks; no target to measure against."
    else:
        status = "on_pace"
        gone = f" with {time_gone:.0%} of the time gone" if time_gone is not None else ""
        facts = f"{progress:.0%} done{gone} ({measured})."
    if goal.deadline and status != "done":
        facts += f" Deadline {goal.deadline:%d %b %Y}."
    return GoalHealth(
        goal.id, goal.title, status, facts, progress, time_gone,
        [t.title for t in open_tasks], [t.title for t in done_this_week],
        goal.current, goal.target,
    )  # fmt: skip


def goals_health(
    goals: Sequence[Goal], open_tasks: Sequence[Task], done: Sequence[Task], today: date
) -> list[GoalHealth]:
    return [
        goal_health(
            g,
            [t for t in open_tasks if g.id in t.goal_ids],
            [t for t in done if g.id in t.goal_ids],
            today,
        )
        for g in goals
    ]


class GoalNote(BaseModel):
    ref: str = Field(description="Reference of a goal, like G2")
    note: str = Field(description="One short sentence on where the goal stands; plain text")
    next_step: str = Field(
        default="",
        description="Only for a goal with no next step: one concrete task title, imperative, "
        "at most 80 characters; otherwise empty",
    )


class GoalAdvice(BaseModel):
    goals: list[GoalNote] = Field(default_factory=list)


SYSTEM = f"""You are the goal specialist of LifeOS, a personal Chief of Staff.
For each goal, write one short sentence on where it stands, from the numbers and tasks given.
- For a goal whose status is no_next_step, suggest ONE concrete task the owner could do this
  week to move it forward, as a short imperative title. Leave next_step empty for every other
  goal.
- Don't invent numbers, dates or tasks that aren't in the data. Plain text, no markdown.
{DATA_RULES.replace("(like T3)", "(like G2)")}
{MEMORY_RULES}"""


@dataclass
class GoalsResult:
    notes: dict[str, str]  # goal id → sentence
    steps: dict[str, str]  # goal id → suggested next task
    source: Literal["ai", "rules"]
    warnings: list[str] = field(default_factory=list)
    usage: list[dict[str, Any]] = field(default_factory=list)


async def advise_goals(
    llm: LLMRouter,
    health: Sequence[GoalHealth],
    *,
    existing_titles: Sequence[str] = (),
    memory: Sequence[str] = (),
) -> GoalsResult:
    if not health:
        return GoalsResult({}, {}, "rules")
    refs = RefMap("G")
    lines = []
    for item in health:
        ref = refs.add(item.goal_id)
        open_titles = "; ".join(clean(t, 80) for t in item.open_tasks[:5]) or "none"
        done_titles = "; ".join(clean(t, 80) for t in item.done_this_week[:5]) or "none"
        lines.append(
            f'{ref} | "{clean(item.title)}" | status {item.status} | {clean(item.facts, 200)}\n'
            f"  open tasks: {open_titles}\n  done this week: {done_titles}"
        )
    prompt = (
        memory_block(memory)
        + data_block("ACTIVE GOALS:\n" + "\n".join(lines))
        + "\n\nWrite a note for each goal."
    )
    call = await ask_model(llm, GoalAdvice, agent="goals", system=SYSTEM, prompt=prompt)
    usage = [call.usage] if call.usage else []
    if call.value is None:
        return GoalsResult({}, {}, "rules", [unavailable_warning(call.error)], usage)
    by_id = {item.goal_id: item for item in health}
    taken = {t.casefold() for t in existing_titles}
    notes: dict[str, str] = {}
    steps: dict[str, str] = {}
    warnings: list[str] = []
    for entry in call.value.goals:
        goal_id = refs.resolve(entry.ref)
        if goal_id is None or goal_id in notes:
            continue
        notes[goal_id] = clean(entry.note, 240)
        step = clean(entry.next_step, 80).rstrip(".")
        if not step or by_id[goal_id].status != "no_next_step":
            continue
        if step.casefold() in taken:
            continue  # already a task: nothing new to suggest
        if len(steps) >= MAX_STEPS:
            warnings.append("More next steps were suggested than the review keeps (three).")
            break
        steps[goal_id] = step
        taken.add(step.casefold())
    return GoalsResult(notes, steps, "ai", warnings, usage)


# --- reading ---------------------------------------------------------------------------------


def reading_pace(book: Book, today: date) -> dict[str, Any]:
    """Pages read, pages a week since it was started, and when it would be finished at that
    pace. Unknown parts are None."""
    read, total = book.pages_read, book.pages
    weekly = None
    finish = None
    if read and book.started and today > book.started:
        weekly = read / max(1, (today - book.started).days) * 7
        if total and total > read and weekly > 0:
            finish = today + timedelta(days=round((total - read) / weekly * 7))
    return {
        "id": book.id,
        "title": book.title,
        "author": book.author,
        "pages_read": read,
        "pages": total,
        "pages_per_week": round(weekly) if weekly is not None else None,
        "finish": finish.isoformat() if finish else None,
    }
