"""Accounts' saved workspaces: the mapping as JSON, and the rule settings.

An account gets a working context once it has an active Notion connection and a saved mapping;
`AccountWorkspaces` builds its workflow dependencies from both.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import NotionConnection, Workspace
from app.notion.client import NotionClient
from app.notion.connections import NotionConnections
from app.notion.mapping import DataSourceSpec
from app.workflows.deps import RuleSettings, WorkflowDeps


def specs_from_mapping(mapping: dict[str, Any]) -> dict[str, DataSourceSpec]:
    return {
        name: DataSourceSpec.model_validate({**body, "logical_name": name})
        for name, body in mapping.items()
    }


def mapping_json(specs: dict[str, DataSourceSpec]) -> dict[str, Any]:
    return {
        name: spec.model_dump(
            mode="json", exclude={"logical_name", "env", "database_env"}, exclude_none=True
        )
        for name, spec in specs.items()
    }


# Builds a WorkflowDeps for an account: (notion client, specs, user id, timezone, rules).
DepsFactory = Callable[[NotionClient, dict[str, DataSourceSpec], uuid.UUID, ZoneInfo, RuleSettings],
                       WorkflowDeps]  # fmt: skip


class AccountWorkspaces:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        connections: NotionConnections,
        make_deps: DepsFactory,
    ) -> None:
        self.session_factory = session_factory
        self.connections = connections
        self.make_deps = make_deps

    async def deps_for(self, user_id: uuid.UUID) -> WorkflowDeps | None:
        """None until the account has connected Notion and saved a mapping. Modules the drift
        check paused are left out."""
        async with self.session_factory() as db:
            workspace = await db.get(Workspace, user_id)
            connection = await db.get(NotionConnection, user_id)
        if workspace is None or connection is None or connection.status != "active":
            return None
        paused = set(workspace.paused_modules or [])
        if "tasks" in paused:
            return None  # the drift check found Tasks no longer matching; set up again
        specs = {n: s for n, s in specs_from_mapping(workspace.mapping).items() if n not in paused}
        return self.make_deps(
            self.connections.client(user_id),
            specs,
            user_id,
            ZoneInfo(workspace.timezone),
            RuleSettings(max_major_per_day=workspace.max_major_per_day),
        )
