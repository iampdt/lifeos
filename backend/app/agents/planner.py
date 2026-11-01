"""Planning specialist: which candidate tasks to commit to on a day (rule R1).

The model only chooses among candidates code has already filtered; its answer is rejected if it
names an unknown task, repeats one, or picks more than the free slots. A rejected or missing
answer falls back to "P0, then P1, then earliest Due" (architecture §5.5).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
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
    fmt_day,
    ref_titles,
    replace_refs,
    task_block,
)

FALLBACK_REASON = "Rule-based pick: highest priority first, then earliest Due."


class PlanPick(BaseModel):
    ref: str = Field(description="Reference of one candidate task, like T2")
    reason: str = Field(description="One short sentence on why this task, for this day")


class DayPlan(BaseModel):
    picks: list[PlanPick] = Field(description="Chosen tasks, most important first")
    note: str = Field(
        default="", description="One or two sentences of focus advice for the day; plain text"
    )


SYSTEM = f"""You are the planning specialist of LifeOS, a personal Chief of Staff.
You choose which candidate tasks the owner commits to on one day.
- Choose at most the number of tasks you are told. Fewer is fine when candidates are weak.
- Prefer higher priority (P0 is highest), closer deadlines, and work that serves active goals.
- When priorities are equal, keep a balance across areas.
- Never choose a task that is not in the candidate list.
- Keep reasons short and concrete. Do not use markdown.
- In reasons and the note, name tasks by their title, never by reference codes.
{DATA_RULES}"""


@dataclass
class PlanResult:
    task_ids: list[str]
    reasons: dict[str, str]
    note: str = ""
    source: Literal["ai", "rules"] = "rules"
    warnings: list[str] = field(default_factory=list)
    usage: list[dict[str, Any]] = field(default_factory=list)


def validate_plan(plan: DayPlan, refs: RefMap, slots: int) -> tuple[list[str], dict[str, str]]:
    """Return (task ids, reasons) or raise ValueError explaining why the plan is rejected."""
    ids: list[str] = []
    reasons: dict[str, str] = {}
    for pick in plan.picks:
        page_id = refs.resolve(pick.ref)
        if page_id is None:
            raise ValueError(f"unknown task reference {clean(pick.ref, 20)!r}")
        if page_id in ids:
            raise ValueError(f"{pick.ref} chosen twice")
        ids.append(page_id)
        reasons[page_id] = clean(pick.reason, 200)
    if len(ids) > slots:
        raise ValueError(f"chose {len(ids)} tasks for {slots} free slots (R1)")
    return ids, reasons


async def plan_day(
    llm: LLMRouter,
    *,
    day: date,
    slots: int,
    candidates: Sequence[Task],
    committed: Sequence[Task] = (),
    goals: Sequence[Goal] = (),
    mode: Literal["fill", "keep"] = "fill",
) -> PlanResult:
    """`candidates` must already be in fallback order (rules.fallback_order).

    mode "fill": choose up to `slots` tasks to add to the day.
    mode "keep": the day is over the limit; choose the `slots` tasks to keep.
    """
    if slots <= 0 or not candidates:
        return PlanResult([], {})
    refs = RefMap("T")
    sections = [task_block("CANDIDATES", refs, candidates)]
    if committed:
        sections.append(task_block("ALREADY COMMITTED THAT DAY", RefMap("C"), committed))
    if goals:
        sections.append("ACTIVE GOALS:\n" + "\n".join(f"- {clean(g.title)}" for g in goals))
    if mode == "keep":
        ask = (
            f"The owner has {len(candidates)} major tasks due on {fmt_day(day)}, more than the "
            f"limit of 3. Choose the {slots} to keep that day; the rest will be offered for "
            "later days."
        )
    else:
        ask = f"Choose up to {slots} candidate tasks for {fmt_day(day)}."
    prompt = f"{ask}\n\n{data_block(*sections)}"

    call = await ask_model(llm, DayPlan, agent="planner", system=SYSTEM, prompt=prompt)
    usage = [call.usage] if call.usage else []
    if call.value is not None:
        try:
            ids, reasons = validate_plan(call.value, refs, slots)
            titles = ref_titles((refs, {t.id: t.title for t in candidates}))
            reasons = {k: clean(replace_refs(v, titles), 200) for k, v in reasons.items()}
            note = clean(replace_refs(call.value.note, titles), 300)
            return PlanResult(ids, reasons, note, "ai", [], usage)
        except ValueError as exc:
            warning = f"AI plan rejected ({exc}); used the rule-based fallback."
    else:
        warning = unavailable_warning(call.error)
    picked = list(candidates)[:slots]
    return PlanResult(
        [t.id for t in picked], {t.id: FALLBACK_REASON for t in picked}, "", "rules",
        [warning], usage,
    )  # fmt: skip
