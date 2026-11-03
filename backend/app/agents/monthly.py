"""Monthly review specialist.

Code pre-computes every number (the month's tasks by area, the scorecard against the previous
month, goal progress). The model writes the narrative: wins, misses, patterns across the weeks,
and at most three things to focus on next month, plus a line per active goal. Without a model
the review is built from the numbers alone.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.agents.common import ask_model, unavailable_warning
from app.agents.reviewer import habits_line, metrics_line
from app.domain.models import Goal, Task
from app.llm.router import LLMRouter
from app.services.context import (
    DATA_RULES,
    RefMap,
    clean,
    data_block,
    ref_titles,
    replace_refs,
    task_block,
)
from app.services.rules import HabitSummary, WeekMetrics
from app.services.scorecard import Scorecard

MAX_ITEMS = 5


class GoalNote(BaseModel):
    ref: str = Field(description="Reference of an active goal, like G2")
    note: str = Field(description="One short sentence on its progress this month")


class MonthlyReviewDraft(BaseModel):
    summary: str = Field(description="Four to six sentences on how the month went; plain text")
    wins: list[str] = Field(default_factory=list, description="What went well")
    misses: list[str] = Field(default_factory=list, description="What slipped")
    patterns: list[str] = Field(
        default_factory=list, description="What helped or got in the way, seen across the weeks"
    )
    focus: list[str] = Field(default_factory=list, description="At most three for next month")
    goal_notes: list[GoalNote] = Field(default_factory=list, description="One per active goal")


SYSTEM = f"""You are the monthly review specialist of LifeOS, a personal Chief of Staff.
Write a short, honest review of the owner's month from the numbers, lists and weekly reviews
provided.
- Use only the facts given. Do not invent tasks, numbers or events.
- Look for patterns across the weeks rather than repeating each week.
- Be direct and specific; no motivational filler, no markdown.
- At most three things to focus on next month, each one line.
- In summary, wins, misses, patterns and focus, name tasks and goals by their title. Never write
  reference codes like T1 or G2 there; codes belong only in goal_notes.
{DATA_RULES}"""


@dataclass
class MonthlyResult:
    summary: str
    wins: list[str]
    misses: list[str]
    patterns: list[str]
    focus: list[str]
    goal_notes: dict[str, str]  # goal id → note
    source: Literal["ai", "rules"] = "rules"
    warnings: list[str] = field(default_factory=list)
    usage: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def goal_line(ref: str, goal: Goal) -> str:
    parts = [f'{ref} | "{clean(goal.title)}"']
    if goal.current is not None and goal.target:
        parts.append(f"progress {goal.current:g} of {goal.target:g}")
    if goal.deadline:
        parts.append(f"deadline {goal.deadline.isoformat()}")
    return " | ".join(parts)


async def draft_month_review(
    llm: LLMRouter,
    *,
    month_label: str,
    days: int,
    metrics: WeekMetrics,
    habits: HabitSummary,
    previous_habits: HabitSummary,
    scorecard: Scorecard,
    done: Sequence[Task],
    slipped: Sequence[Task],
    goals: Sequence[Goal] = (),
    weekly_reviews: Sequence[str] = (),
) -> MonthlyResult:
    done_refs, slipped_refs, goal_refs = RefMap("D"), RefMap("T"), RefMap("G")
    areas = "\n".join(
        f"- {clean(area, 40)}: {c.done}/{c.planned}" for area, c in metrics.by_area.items()
    )
    sections = [
        f"MONTH: {month_label}\nTASKS: {metrics_line(metrics)}\nBY AREA (done/planned):\n"
        + (areas or "(none)"),
        *(
            ["HABITS THIS MONTH: " + habits_line(habits, scorecard, days),
             "HABITS LAST MONTH: " + habits_line(previous_habits, scorecard, days)]
            if scorecard else []
        ),
        task_block("DONE THIS MONTH", done_refs, done[:40]),
        task_block("DUE THIS MONTH, STILL OPEN", slipped_refs, slipped[:20]),
    ]  # fmt: skip
    if goals:
        lines = [goal_line(goal_refs.add(g.id), g) for g in goals]
        sections.append("ACTIVE GOALS:\n" + "\n".join(lines))
    if weekly_reviews:
        sections.append(
            "THIS MONTH'S WEEKLY REVIEWS:\n"
            + "\n---\n".join(clean(text, 600) for text in weekly_reviews[:5])
        )
    prompt = data_block(*sections) + "\n\nWrite the monthly review."

    call = await ask_model(
        llm, MonthlyReviewDraft, agent="monthly", system=SYSTEM, prompt=prompt, tier="smart"
    )
    if call.value is None:
        return MonthlyResult(
            summary=metrics_line(metrics) + " AI summary unavailable.",
            wins=[clean(t.title) for t in done[:MAX_ITEMS]],
            misses=[clean(t.title) for t in slipped[:MAX_ITEMS]],
            patterns=[],
            focus=[],
            goal_notes={},
            warnings=[unavailable_warning(call.error)],
        )

    draft = call.value
    titles = ref_titles(
        (done_refs, {t.id: t.title for t in done}),
        (slipped_refs, {t.id: t.title for t in slipped}),
        (goal_refs, {g.id: g.title for g in goals}),
    )

    def prose(value: str, limit: int = 200) -> str:
        return clean(replace_refs(value, titles), limit)

    def items(values: list[str], limit: int = MAX_ITEMS) -> list[str]:
        return [prose(v) for v in values if prose(v)][:limit]

    warnings: list[str] = []
    notes: dict[str, str] = {}
    for item in draft.goal_notes:
        goal_id = goal_refs.resolve(item.ref)
        if goal_id is None:
            warnings.append(f"Ignored a note for unknown goal {clean(item.ref, 20)!r}.")
        elif prose(item.note):
            notes[goal_id] = prose(item.note)
    return MonthlyResult(
        summary=prose(draft.summary, 1000),
        wins=items(draft.wins),
        misses=items(draft.misses),
        patterns=items(draft.patterns),
        focus=items(draft.focus, 3),
        goal_notes=notes,
        source="ai",
        warnings=warnings,
        usage=[call.usage] if call.usage else [],
    )


def render_month_review(
    month_label: str,
    review: dict[str, Any],
    metrics: WeekMetrics,
    goal_titles: dict[str, str],
    owner_note: str | None = None,
) -> str:
    """Plain-text Review written to the month's page (month.close)."""
    lines = [f"LifeOS monthly review · {month_label}", metrics_line(metrics), ""]
    if review.get("summary"):
        lines += [review["summary"], ""]
    for title, key in (("Wins", "wins"), ("Missed", "misses"), ("Patterns", "patterns")):
        if review.get(key):
            lines += [f"{title}:", *[f"• {item}" for item in review[key]], ""]
    if review.get("focus"):
        lines += ["Next month:", *[f"{i}. {f}" for i, f in enumerate(review["focus"], 1)], ""]
    notes = review.get("goal_notes") or {}
    if notes:
        lines += ["Goals:", *[f"• {goal_titles.get(g, 'Goal')}: {n}" for g, n in notes.items()], ""]
    if owner_note:
        lines += ["Owner's note: " + owner_note.strip()]
    return "\n".join(lines).strip()
