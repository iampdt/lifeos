"""Bundle the configured repositories behind one object (the read side of `NotionGateway`)."""

from __future__ import annotations

from dataclasses import dataclass
from zoneinfo import ZoneInfo

from app.notion.client import NotionClient
from app.notion.mapping import DataSourceSpec
from app.notion.repositories import (
    BookRepository,
    GoalRepository,
    HabitRepository,
    PeriodRepository,
    TaskRepository,
)
from app.services.calendar import DEFAULT_TZ


@dataclass
class NotionWorkspace:
    tasks: TaskRepository | None = None
    goals: GoalRepository | None = None
    habits: HabitRepository | None = None
    weeks: PeriodRepository | None = None
    months: PeriodRepository | None = None
    books: BookRepository | None = None


_REPOSITORIES = {
    "tasks": TaskRepository,
    "goals": GoalRepository,
    "habits": HabitRepository,
    "weeks": PeriodRepository,
    "months": PeriodRepository,
    "books": BookRepository,
}


def build_workspace(
    client: NotionClient, specs: dict[str, DataSourceSpec], tz: ZoneInfo = DEFAULT_TZ
) -> NotionWorkspace:
    """Create a repository for every configured data source; unconfigured ones stay None."""
    workspace = NotionWorkspace()
    for logical, repo_cls in _REPOSITORIES.items():
        spec = specs.get(logical)
        if spec and spec.configured:
            setattr(workspace, logical, repo_cls(client, spec, tz))
    return workspace
