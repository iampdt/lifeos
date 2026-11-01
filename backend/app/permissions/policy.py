"""What LifeOS may write, and how each action kind is handled (architecture §6.3).

The allowlist is code-reviewed configuration: a write that touches any field not listed here
is refused by the executor, whatever the proposal says.
"""

from __future__ import annotations

from enum import StrEnum

from app.execution.actions import HABIT_CHECKS, ActionKind, WritePlan

WRITE_ALLOWLIST: dict[str, frozenset[str]] = {
    "tasks": frozenset(
        {"title", "status", "due", "completed", "priority", "area", "goal", "week", "month"}
    ),
    "habits": frozenset({"title", "sleep", "screen_time", "notes", *HABIT_CHECKS}),
    "weeks": frozenset({"title", "start", "end", "status", "theme", "review"}),
    "months": frozenset({"title", "start", "end", "status", "theme", "review"}),
    "books": frozenset({"status", "pages_read", "started", "finished"}),
    "goals": frozenset({"current"}),
}


class Mode(StrEnum):
    OFF = "off"  # never proposed
    SUGGEST = "suggest"  # shown to the owner, can't be executed by LifeOS
    APPROVE = "approve"  # executed after the owner approves (default)
    AUTO = "auto"  # reserved; not enabled in v1


DEFAULT_MODES: dict[ActionKind, Mode] = {kind: Mode.APPROVE for kind in ActionKind}


class PermissionDenied(Exception):
    pass


def check_plan(plan: WritePlan) -> None:
    allowed = WRITE_ALLOWLIST.get(plan.data_source, frozenset())
    refused = sorted(set(plan.values) - allowed)
    if refused:
        raise PermissionDenied(f"{plan.data_source}: writing {refused} is not allowed")
