"""Which workspace each account works on, and the runner that serves it.

Until accounts connect their own Notion (multi-user step 9.3), a deployment has one workspace,
configured in the environment, and only the owner account (`User.is_owner`) uses it. Other
accounts can sign in but have no workspace yet. In the public demo, the owner is the shared
demo account.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass

from langgraph.checkpoint.base import BaseCheckpointSaver

from app.workflows.deps import WorkflowDeps
from app.workflows.runner import WorkflowRunner


@dataclass(frozen=True)
class UserContext:
    deps: WorkflowDeps
    runner: WorkflowRunner


class Contexts:
    def __init__(
        self,
        make_deps: Callable[[uuid.UUID], WorkflowDeps],
        checkpointer: BaseCheckpointSaver,
    ) -> None:
        self._make_deps = make_deps
        self.checkpointer = checkpointer
        self._cache: dict[uuid.UUID, UserContext] = {}

    def for_user(self, user_id: uuid.UUID, *, is_owner: bool) -> UserContext | None:
        """The account's dependencies and runner, or None when it has no workspace."""
        if not is_owner:
            return None
        context = self._cache.get(user_id)
        if context is None:
            deps = self._make_deps(user_id)
            context = UserContext(deps, WorkflowRunner(deps, self.checkpointer))
            self._cache[user_id] = context
        return context

    def forget(self, user_id: uuid.UUID) -> None:
        self._cache.pop(user_id, None)
