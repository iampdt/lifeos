"""What each module of a workspace needs (multi-user §Generic workspace model).

A mapping names, for each module an account uses, the data source that holds it and the
property behind each field. Tasks are required; habits, weekly and monthly reviews, goals and
reading are optional, and the workflows skip whatever isn't mapped.

Status-like fields map semantic keys (`next`, `done`, …) to the account's own option names, so
no rule depends on what anyone calls their statuses.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.notion.mapping import DataSourceSpec

CHOICE = frozenset({"select", "status"})
PRIORITY_KEY = re.compile(r"^p\d$")


@dataclass(frozen=True)
class FieldRule:
    types: frozenset[str]
    required: bool = False
    # For status-like fields: the semantic keys that must be, and may be, mapped to options.
    needs: tuple[str, ...] = ()
    allows: tuple[str, ...] = ()


@dataclass(frozen=True)
class Module:
    label: str
    required: bool
    fields: dict[str, FieldRule]
    # Habits: any number of checkbox (habit) and number (metric) properties besides `fields`.
    free_fields: bool = False
    notes: dict[str, str] = field(default_factory=dict)


def _f(*types: str, required: bool = False, needs=(), allows=()) -> FieldRule:
    return FieldRule(frozenset(types), required, tuple(needs), tuple(allows))


PERIOD_FIELDS = {
    "title": _f("title", required=True),
    "start": _f("date", required=True),
    "end": _f("date", required=True),
    "status": _f(*CHOICE, allows=("upcoming", "current", "closed")),
    "review": _f("rich_text"),
    "theme": _f("rich_text"),
    "tasks": _f("relation"),
}

MODULES: dict[str, Module] = {
    "tasks": Module(
        "Tasks",
        True,
        {
            "title": _f("title", required=True),
            "status": _f(
                *CHOICE,
                required=True,
                needs=("next", "done"),
                allows=("inbox", "doing", "waiting", "cancelled"),
            ),  # fmt: skip
            "due": _f("date", required=True),
            "priority": _f(*CHOICE),  # values p0, p1, … from highest to lowest
            "area": _f("select"),
            "completed": _f("date"),
            "recurring": _f("checkbox"),
            "goal": _f("relation"),
            "week": _f("relation"),
            "month": _f("relation"),
            "created": _f("created_time"),
            # Formulas computing the week and month keys, read for comparison (architecture §4).
            "week_key": _f("formula"),
            "month_key": _f("formula"),
        },
    ),
    "habits": Module(
        "Habits",
        False,
        {"title": _f("title", required=True), "day": _f("date"), "notes": _f("rich_text")},
        free_fields=True,
    ),
    "weeks": Module("Weekly reviews", False, PERIOD_FIELDS),
    "months": Module("Monthly reviews", False, PERIOD_FIELDS),
    "goals": Module(
        "Goals",
        False,
        {
            "title": _f("title", required=True),
            "status": _f(
                *CHOICE, required=True, needs=("active",), allows=("planned", "done", "dropped")
            ),  # fmt: skip
            "type": _f(*CHOICE),
            "area": _f("select"),
            "priority": _f(*CHOICE),
            "current": _f("number"),
            "target": _f("number"),
            "success_metric": _f("rich_text"),
            "start": _f("date"),
            "deadline": _f("date"),
            "review_notes": _f("rich_text"),
        },
    ),
    "books": Module(
        "Reading",
        False,
        {
            "title": _f("title", required=True),
            "status": _f(
                *CHOICE, required=True, needs=("reading",), allows=("want", "finished", "abandoned")
            ),  # fmt: skip
            "author": _f("rich_text"),
            "lane": _f("select"),
            "up_next": _f("number"),
            "area": _f("select"),
            "shelf": _f("multi_select"),
            "pages": _f("number"),
            "pages_read": _f("number"),
            "started": _f("date"),
            "finished": _f("date"),
        },
    ),
}


@dataclass(frozen=True)
class MappingIssue:
    module: str
    field: str | None
    problem: str

    def __str__(self) -> str:
        where = f"{self.module}.{self.field}" if self.field else self.module
        return f"{where}: {self.problem}"


def validate_mapping(specs: dict[str, DataSourceSpec]) -> list[MappingIssue]:
    """Structural checks, before anything is compared with the live workspace."""
    issues: list[MappingIssue] = []
    for name in specs:
        if name not in MODULES:
            issues.append(MappingIssue(name, None, "unknown module"))
    for name, module in MODULES.items():
        spec = specs.get(name)
        if spec is None:
            if module.required:
                issues.append(MappingIssue(name, None, "required"))
            continue
        if not spec.data_source_id:
            issues.append(MappingIssue(name, None, "no data source chosen"))
        issues.extend(_check_fields(name, module, spec))
    return issues


def _check_fields(name: str, module: Module, spec: DataSourceSpec) -> list[MappingIssue]:
    issues: list[MappingIssue] = []
    for field_name, rule in module.fields.items():
        if rule.required and field_name not in spec.properties:
            issues.append(MappingIssue(name, field_name, "required"))
    seen: dict[str, str] = {}
    for field_name, prop in spec.properties.items():
        if prop.name in seen:
            issues.append(
                MappingIssue(name, field_name, f"{prop.name!r} is also {seen[prop.name]}")
            )
        seen[prop.name] = field_name
        rule = module.fields.get(field_name)
        if rule is None:
            if not (module.free_fields and prop.type in ("checkbox", "number")):
                issues.append(MappingIssue(name, field_name, "unknown field"))
            continue
        if prop.type not in rule.types:
            issues.append(MappingIssue(name, field_name, f"can't be a {prop.type} property"))
        if field_name == "priority":
            bad = [k for k in prop.values if not PRIORITY_KEY.match(k)]
            if bad:
                issues.append(
                    MappingIssue(name, field_name, f"priority keys must be p0, p1, …: {bad}")
                )
            continue
        for key in rule.needs:
            if key not in prop.values:
                issues.append(
                    MappingIssue(name, field_name, f"choose the option that means {key!r}")
                )
        allowed = set(rule.needs) | set(rule.allows)
        for key in prop.values:
            if allowed and key not in allowed:
                issues.append(MappingIssue(name, field_name, f"unknown meaning {key!r}"))
        options = list(prop.values.values())
        if len(options) != len(set(options)):
            issues.append(MappingIssue(name, field_name, "an option is used for two meanings"))
    if module.free_fields and not (spec.habit_fields or spec.metric_fields):
        issues.append(MappingIssue(name, None, "needs at least one checkbox or number property"))
    return issues
