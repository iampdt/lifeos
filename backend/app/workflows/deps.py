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
from app.services.rules import Vocabulary


def utcnow() -> datetime:
    return datetime.now(UTC)


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
    workspace: NotionWorkspace = field(init=False)
    vocab: Vocabulary = field(init=False)

    def __post_init__(self) -> None:
        self.workspace = build_workspace(self.notion, self.specs, self.tz)
        self.vocab = Vocabulary.from_spec(self.specs["tasks"])
        missing = [
            n for n in ("tasks", "habits", "weeks", "months") if not self.specs[n].configured
        ]
        if missing:
            raise ValueError(f"workflows need these data sources configured: {missing}")

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
        assert self.workspace.weeks and self.workspace.months
        return PeriodResolver(self.workspace.weeks, self.workspace.months, today)
