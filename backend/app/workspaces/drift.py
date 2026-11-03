"""The daily drift check for accounts' workspaces (multi-user §Generic workspace model).

Each module's data source is compared with its saved mapping. A module that no longer matches
(a renamed or deleted property, a removed option, a database LifeOS can't see any more) is
paused: it is left out of the account's workspace until the mapping is saved again. If Tasks is
paused, the account has no workspace until then. Nothing is written to Notion.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Workspace
from app.execution.audit import audit
from app.notion.connections import NotionConnections
from app.notion.errors import NotionNotShared
from app.notion.schema import check_data_source
from app.workspaces.store import specs_from_mapping


async def check_drift(
    session_factory: async_sessionmaker[AsyncSession],
    connections: NotionConnections,
    user_id: uuid.UUID,
    clock: Callable[[], datetime],
) -> list[str] | None:
    """Checks every module and records the result. Returns the paused modules, or None when
    the account has no saved workspace. A network failure or a lost connection raises, so the
    check runs again later instead of pausing anything."""
    async with session_factory() as db:
        workspace = await db.get(Workspace, user_id)
    if workspace is None:
        return None
    drift: dict[str, list[str]] = {}
    client = connections.client(user_id)
    try:
        for name, spec in specs_from_mapping(workspace.mapping).items():
            try:
                report = await check_data_source(client, spec)
            except NotionNotShared:
                drift[name] = ["LifeOS can't see this database any more"]
                continue
            problems = [
                f"{i.property_name!r}: {i.problem} {i.detail}".strip()
                for i in report.issues
                if i.blocking
            ]
            if problems:
                drift[name] = problems
    finally:
        await client.aclose()
    paused = sorted(drift)
    async with session_factory() as db, db.begin():
        workspace = await db.get(Workspace, user_id)
        assert workspace is not None
        changed = paused != sorted(workspace.paused_modules or [])
        workspace.paused_modules = paused
        workspace.drift = drift or None
        workspace.checked_at = clock()
        if changed:
            audit(db, actor="system", event="workspace.drift", user_id=user_id,
                  details={"paused": paused})  # fmt: skip
    return paused
