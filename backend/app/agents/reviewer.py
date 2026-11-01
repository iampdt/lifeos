"""Weekly review specialist (design §8.3, MVP).

Code pre-computes every number (R5 metrics, habit counts, the overdue queue). The model writes
the narrative and recommends, for each overdue task, carry forward or cancel. The owner makes
the actual decision; without a model the review is built from the numbers alone.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.agents.common import ask_model, unavailable_warning
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


class CarryAdvice(BaseModel):
    ref: str = Field(description="Reference of an overdue task, like T4")
    decision: Literal["carry_forward", "cancel"]
    reason: str = Field(description="One short sentence")


class WeeklyReviewDraft(BaseModel):
    summary: str = Field(description="Three to five sentences on how the week went; plain text")
    wins: list[str] = Field(default_factory=list, description="Accomplishments worth noting")
    misses: list[str] = Field(default_factory=list, description="Commitments that slipped")
    bottlenecks: list[str] = Field(default_factory=list, description="What got in the way")
    priorities: list[str] = Field(
        default_factory=list, description="At most three priorities for next week"
    )
    advice: list[CarryAdvice] = Field(
        default_factory=list, description="One entry per overdue task"
    )


SYSTEM = f"""You are the weekly review specialist of LifeOS, a personal Chief of Staff.
Write a short, honest review of the owner's week from the numbers and lists provided.
- Use only the facts given. Do not invent tasks, numbers or events.
- Be direct and specific; no motivational filler, no markdown.
- For every overdue task, recommend carry_forward (still worth doing soon) or cancel (no longer
  relevant or repeatedly postponed with low priority). The owner decides.
- At most three priorities for next week, each one line.
- In summary, wins, misses, bottlenecks and priorities, name tasks by their title. Never write
  reference codes like T1 or D2 there; codes belong only in the advice list.
{DATA_RULES}"""

MAX_ITEMS = 5


@dataclass
class ReviewResult:
    summary: str
    wins: list[str]
    misses: list[str]
    bottlenecks: list[str]
    priorities: list[str]
    advice: dict[str, dict[str, str]]  # task id → {decision, reason}
    source: Literal["ai", "rules"] = "rules"
    warnings: list[str] = field(default_factory=list)
    usage: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def metrics_line(metrics: WeekMetrics) -> str:
    rate = metrics.completion_rate
    pct = f" ({rate:.0%})" if rate is not None else ""
    return (
        f"Done {metrics.done} of {metrics.planned} planned{pct}; "
        f"{metrics.still_open} still open; {metrics.cancelled} cancelled."
    )


def habits_line(habits: HabitSummary) -> str:
    checks = ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in habits.checks.items()) or "none"
    parts = [f"{habits.days_logged} of 7 days logged; checks: {checks}"]
    if habits.avg_sleep is not None:
        parts.append(f"avg sleep {habits.avg_sleep} h")
    if habits.avg_screen_time is not None:
        parts.append(f"avg screen time {habits.avg_screen_time} h")
    return "; ".join(parts) + "."


def fallback_review(
    metrics: WeekMetrics, overdue: Sequence[Task], done: Sequence[Task], warning: str
) -> ReviewResult:
    return ReviewResult(
        summary=metrics_line(metrics) + " AI summary unavailable.",
        wins=[clean(t.title) for t in done[:MAX_ITEMS]],
        misses=[clean(t.title) for t in overdue[:MAX_ITEMS]],
        bottlenecks=[],
        priorities=[],
        advice={
            t.id: {"decision": "carry_forward", "reason": "Default: carry forward."}
            for t in overdue
        },  # fmt: skip
        source="rules",
        warnings=[warning],
    )


async def draft_review(
    llm: LLMRouter,
    *,
    week_label: str,
    metrics: WeekMetrics,
    habits: HabitSummary,
    previous_habits: HabitSummary,
    done: Sequence[Task],
    overdue: Sequence[Task],
    goals: Sequence[Goal] = (),
) -> ReviewResult:
    refs, done_refs = RefMap("T"), RefMap("D")
    areas = "\n".join(
        f"- {clean(area, 40)}: {c.done}/{c.planned}" for area, c in metrics.by_area.items()
    )
    sections = [
        f"WEEK: {week_label}\nTASKS: {metrics_line(metrics)}\nBY AREA (done/planned):\n"
        + (areas or "(none)"),
        "HABITS THIS WEEK: " + habits_line(habits),
        "HABITS LAST WEEK: " + habits_line(previous_habits),
        task_block("DONE THIS WEEK", done_refs, done[:25]),
        task_block("OVERDUE (needs a decision)", refs, overdue),
    ]
    if goals:
        sections.append("ACTIVE GOALS:\n" + "\n".join(f"- {clean(g.title)}" for g in goals))
    prompt = data_block(*sections) + "\n\nWrite the weekly review."

    call = await ask_model(
        llm, WeeklyReviewDraft, agent="reviewer", system=SYSTEM, prompt=prompt, tier="smart"
    )
    if call.value is None:
        return fallback_review(metrics, overdue, done, unavailable_warning(call.error))

    draft = call.value
    warnings: list[str] = []
    advice: dict[str, dict[str, str]] = {}
    for item in draft.advice:
        page_id = refs.resolve(item.ref)
        if page_id is None:
            warnings.append(f"Ignored advice for unknown task {clean(item.ref, 20)!r}.")
            continue
        advice[page_id] = {"decision": item.decision, "reason": clean(item.reason, 200)}
    for task in overdue:  # every overdue task gets a recommendation
        advice.setdefault(
            task.id, {"decision": "carry_forward", "reason": "No AI advice; default."}
        )

    titles = ref_titles(
        (refs, {t.id: t.title for t in overdue}), (done_refs, {t.id: t.title for t in done})
    )

    def prose(value: str, limit: int) -> str:
        return clean(replace_refs(value, titles), limit)

    def items(values: list[str], limit: int = MAX_ITEMS) -> list[str]:
        return [prose(v, 200) for v in values if prose(v, 200)][:limit]

    for item in advice.values():
        item["reason"] = prose(item["reason"], 200)
    return ReviewResult(
        summary=prose(draft.summary, 800),
        wins=items(draft.wins),
        misses=items(draft.misses),
        bottlenecks=items(draft.bottlenecks),
        priorities=items(draft.priorities, 3),
        advice=advice,
        source="ai",
        warnings=warnings,
        usage=[call.usage] if call.usage else [],
    )


def render_review(
    week_label: str,
    review: dict[str, Any],
    metrics: WeekMetrics,
    habits: HabitSummary,
    owner_note: str | None = None,
) -> str:
    """Plain-text Review written to the Weekly Progress page (week.close)."""
    lines = [f"LifeOS weekly review · {week_label}", metrics_line(metrics), ""]
    if review.get("summary"):
        lines += [review["summary"], ""]
    for title, key in (("Wins", "wins"), ("Missed", "misses"), ("Bottlenecks", "bottlenecks")):
        if review.get(key):
            lines += [f"{title}:", *[f"• {item}" for item in review[key]], ""]
    if review.get("priorities"):
        lines += ["Next week:", *[f"{i}. {p}" for i, p in enumerate(review["priorities"], 1)], ""]
    lines.append("Habits: " + habits_line(habits))
    if owner_note:
        lines += ["", "Owner's note: " + owner_note.strip()]
    return "\n".join(lines).strip()
