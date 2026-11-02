"""What LifeOS may write, and how each action kind is handled (architecture §6.3).

The allowlist is code-reviewed configuration: a write that touches any field not listed here
is refused by the executor, whatever the proposal says.
"""

from __future__ import annotations

from enum import StrEnum

from app.execution.actions import ActionKind, WritePlan
from app.notion.mapping import DataSourceSpec

# Habits allow these plus every habit (checkbox) and metric (number) the mapping has.
HABIT_FIELDS = frozenset({"title", "day", "notes"})

WRITE_ALLOWLIST: dict[str, frozenset[str]] = {
    "tasks": frozenset(
        {"title", "status", "due", "completed", "priority", "area", "goal", "week", "month"}
    ),
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


def allowed_fields(data_source: str, spec: DataSourceSpec) -> frozenset[str]:
    if data_source == "habits":
        return HABIT_FIELDS | set(spec.habit_fields) | set(spec.metric_fields)
    return WRITE_ALLOWLIST.get(data_source, frozenset())


def check_plan(plan: WritePlan, spec: DataSourceSpec) -> None:
    allowed = allowed_fields(plan.data_source, spec)
    refused = sorted(set(plan.values) - allowed)
    if refused:
        raise PermissionDenied(f"{plan.data_source}: writing {refused} is not allowed")
