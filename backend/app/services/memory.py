"""An account's memory: short notes it asked LifeOS to remember (architecture §5.8).

Notes are written by the account, on the Memory card or by confirming a suggestion from chat,
never by a model on its own. They go into the planning, review and chat prompts as
preferences (`context.memory_block`). The audit log records that a note changed, not its text,
so deleting a note removes its words.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import MemoryFact, User
from app.execution.audit import audit

MAX_NOTES = 30
MAX_CHARS = 200
SOURCES = ("you", "chat")

_SPACE = re.compile(r"\s+")


class MemoryFull(Exception):
    """The account already has `MAX_NOTES` notes."""


def normalise(text: str) -> str:
    """One line, trimmed; raises ValueError when empty or longer than `MAX_CHARS`."""
    flat = _SPACE.sub(" ", text).strip()
    if not flat:
        raise ValueError("a note can't be empty")
    if len(flat) > MAX_CHARS:
        raise ValueError(f"a note can be at most {MAX_CHARS} characters")
    return flat


async def list_notes(
    session_factory: async_sessionmaker[AsyncSession], user_id: uuid.UUID
) -> list[MemoryFact]:
    async with session_factory() as db:
        rows = await db.scalars(
            select(MemoryFact)
            .where(MemoryFact.user_id == user_id)
            .order_by(MemoryFact.created_at, MemoryFact.id)
        )
        return list(rows)


async def add_note(
    session_factory: async_sessionmaker[AsyncSession],
    user_id: uuid.UUID,
    text: str,
    source: str = "you",
) -> MemoryFact:
    now = datetime.now(UTC)
    note = MemoryFact(id=uuid.uuid4(), user_id=user_id, text=normalise(text), source=source,
                      created_at=now, updated_at=now)  # fmt: skip
    async with session_factory() as db, db.begin():
        # Lock the account's row so two adds can't both pass the limit.
        await db.get(User, user_id, with_for_update=True)
        count = await db.scalar(
            select(func.count()).select_from(MemoryFact).where(MemoryFact.user_id == user_id)
        )
        if (count or 0) >= MAX_NOTES:
            raise MemoryFull
        db.add(note)
        audit(db, actor="user", event="memory.add", user_id=user_id, entity_type="memory_note",
              entity_id=str(note.id), details={"source": source})  # fmt: skip
    return note


async def edit_note(
    session_factory: async_sessionmaker[AsyncSession],
    user_id: uuid.UUID,
    note_id: uuid.UUID,
    text: str,
) -> MemoryFact | None:
    flat = normalise(text)
    async with session_factory() as db, db.begin():
        note = await db.scalar(
            select(MemoryFact).where(MemoryFact.id == note_id, MemoryFact.user_id == user_id)
        )
        if note is None:
            return None
        note.text = flat
        note.updated_at = datetime.now(UTC)
        audit(db, actor="user", event="memory.edit", user_id=user_id,
              entity_type="memory_note", entity_id=str(note_id))  # fmt: skip
    return note


async def delete_note(
    session_factory: async_sessionmaker[AsyncSession], user_id: uuid.UUID, note_id: uuid.UUID
) -> bool:
    async with session_factory() as db, db.begin():
        result = await db.execute(
            delete(MemoryFact).where(MemoryFact.id == note_id, MemoryFact.user_id == user_id)
        )
        if not result.rowcount:
            return False
        audit(db, actor="user", event="memory.delete", user_id=user_id,
              entity_type="memory_note", entity_id=str(note_id))  # fmt: skip
    return True
