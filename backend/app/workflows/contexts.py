"""Which workspace each account works on, and the runner that serves it.

- The owner account (`User.is_owner`) uses the workspace configured in the environment. In the
  public demo, the owner is the shared demo account.
- Any other account gets one once it has connected its own Notion and saved a mapping
  (`app/workspaces/store.py`). Until then it can sign in but has no workspace.

Contexts are built on first use and cached; `forget` drops one after the account's connection
or mapping changes.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from langgraph.checkpoint.base import BaseCheckpointSaver

from app.workflows.deps import WorkflowDeps
from app.workflows.runner import WorkflowRunner


@dataclass(frozen=True)
class UserContext:
    deps: WorkflowDeps
    runner: WorkflowRunner
    owns_client: bool = False  # an account's own Notion client, closed when forgotten


class Contexts:
    def __init__(
        self,
        owner_deps: Callable[[uuid.UUID], WorkflowDeps],
        checkpointer: BaseCheckpointSaver,
        *,
        account_deps: Callable[[uuid.UUID], Awaitable[WorkflowDeps | None]] | None = None,
    ) -> None:
        self._owner_deps = owner_deps
        self._account_deps = account_deps
        self.checkpointer = checkpointer
        self._cache: dict[uuid.UUID, UserContext] = {}

    async def for_user(self, user_id: uuid.UUID, *, is_owner: bool) -> UserContext | None:
        """The account's dependencies and runner, or None when it has no workspace."""
        context = self._cache.get(user_id)
        if context is not None:
            return context
        if is_owner:
            deps = self._owner_deps(user_id)
        elif self._account_deps is not None:
            deps = await self._account_deps(user_id)
        else:
            deps = None
        if deps is None:
            return None
        context = UserContext(deps, WorkflowRunner(deps, self.checkpointer), not is_owner)
        self._cache[user_id] = context
        return context

    async def forget(self, user_id: uuid.UUID) -> None:
        context = self._cache.pop(user_id, None)
        # The owner's client is shared by the deployment and stays open.
        if context is not None and context.owns_client:
            await context.deps.notion.aclose()
