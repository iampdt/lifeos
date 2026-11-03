"""An account's permission mode per action kind (architecture §6.3), stored in
`account_settings`. Anything not chosen needs approval; `auto` is offered only for the kinds in
`AUTO_ALLOWED`."""

from __future__ import annotations

import uuid

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import AccountSettings
from app.execution.actions import ActionKind
from app.execution.audit import audit
from app.permissions.policy import AUTO_ALLOWED, DEFAULT_MODES, Mode

EDITABLE = frozenset({Mode.APPROVE, Mode.SUGGEST, Mode.OFF})


def choosable(kind: ActionKind, mode: Mode) -> bool:
    return mode in EDITABLE or (mode == Mode.AUTO and kind in AUTO_ALLOWED)


async def load_modes(
    session_factory: async_sessionmaker[AsyncSession], user_id: uuid.UUID
) -> dict[ActionKind, Mode]:
    async with session_factory() as db:
        row = await db.get(AccountSettings, user_id)
    modes = dict(DEFAULT_MODES)
    for kind, mode in (row.action_modes if row else {}).items():
        try:
            if choosable(ActionKind(kind), Mode(mode)):
                modes[ActionKind(kind)] = Mode(mode)
        except ValueError:
            continue  # a kind or mode that no longer exists
    return modes


async def save_modes(
    session_factory: async_sessionmaker[AsyncSession],
    user_id: uuid.UUID,
    changes: dict[ActionKind, Mode],
) -> dict[ActionKind, Mode]:
    current = await load_modes(session_factory, user_id)
    current.update(changes)
    stored = {str(k): str(v) for k, v in current.items() if v != Mode.APPROVE}
    async with session_factory() as db, db.begin():
        await db.execute(
            insert(AccountSettings)
            .values(user_id=user_id, action_modes=stored)
            .on_conflict_do_update(index_elements=["user_id"], set_={"action_modes": stored})
        )
        audit(db, actor="user", event="settings.modes", user_id=user_id,
              details={str(k): str(v) for k, v in changes.items()})  # fmt: skip
    return current
