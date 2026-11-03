"""Everything a workflow needs for one account, passed to the graph builders.

Dependencies live here, not in graph state: state is checkpointed to Postgres, so it holds
only JSON-friendly data. Every database row a workflow creates or reads belongs to `user_id`.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.execution.executor import Executor
from app.execution.proposals import ProposalService
from app.llm.router import LLMRouter
from app.notion.mapping import DataSourceSpec
from app.notion.workspace import NotionWorkspace, build_workspace
from app.services.calendar import DEFAULT_TZ
from app.services.periods import PeriodResolver
from app.services.rules import MAX_MAJOR_PER_DAY, Vocabulary
from app.services.scorecard import Scorecard


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class RuleSettings:
    """A workspace's parameters for the domain rules; the rule IDs stay the same."""

    max_major_per_day: int = MAX_MAJOR_PER_DAY  # R1


@dataclass
class WorkflowDeps:
    session_factory: async_sessionmaker[AsyncSession]
    notion: Any  # NotionClient, or FakeNotion in tests
    specs: dict[str, DataSourceSpec]
    llm: LLMRouter
    writes_enabled: bool
    user_id: uuid.UUID
    tz: ZoneInfo = DEFAULT_TZ
    clock: Callable[[], datetime] = utcnow
    rules: RuleSettings = field(default_factory=RuleSettings)
    # "environment" (NOTION_TOKEN and the YAML mapping) or "account" (OAuth and a saved mapping).
    workspace_source: str = "environment"
    workspace: NotionWorkspace = field(init=False)
    vocab: Vocabulary = field(init=False)
    scorecard: Scorecard = field(init=False)

    def __post_init__(self) -> None:
        tasks = self.specs.get("tasks")
        if tasks is None or not tasks.configured:
            raise ValueError("workflows need a tasks data source")
        # Every other module is optional; the workflows skip what isn't mapped.
        self.workspace = build_workspace(self.notion, self.specs, self.tz)
        self.vocab = Vocabulary.from_spec(tasks)
        self.scorecard = Scorecard.from_spec(self.specs.get("habits"))

    def today(self) -> date:
        return self.clock().astimezone(self.tz).date()

    def executor(self, actor: str) -> Executor:
        return Executor(
            self.session_factory,
            self.notion,
            self.specs,
            user_id=self.user_id,
            writes_enabled=self.writes_enabled,
            tz=self.tz,
            actor=actor,
            clock=self.clock,
        )

    def proposals(self, session: AsyncSession) -> ProposalService:
        return ProposalService(session, user_id=self.user_id, clock=self.clock, tz=self.tz)

    def resolver(self, today: date) -> PeriodResolver:
        """Tasks link to weekly and monthly pages only where the workspace has both the
        module and the task relation (R7)."""
        tasks = self.specs["tasks"]
        return PeriodResolver(
            self.workspace.weeks if tasks.has("week") else None,
            self.workspace.months if tasks.has("month") else None,
            today,
        )
