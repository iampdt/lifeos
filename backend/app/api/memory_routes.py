"""An account's memory: the notes LifeOS keeps in mind when it plans, reviews and chats.

Notes belong to the account, not to its workspace, so these routes work before a workspace is
set up. The shared demo's notes are read-only: every visitor would see what another wrote.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field

from app.api.errors import ApiError
from app.api.security import CurrentUser, services
from app.db.models import MemoryFact
from app.services.memory import (
    MAX_CHARS,
    MAX_NOTES,
    MemoryFull,
    add_note,
    delete_note,
    edit_note,
    list_notes,
)

router = APIRouter()


class NoteBody(BaseModel):
    text: str = Field(max_length=1000)
    source: Literal["you", "chat"] = "you"


class EditBody(BaseModel):
    text: str = Field(max_length=1000)


def _view(note: MemoryFact) -> dict[str, Any]:
    return {
        "id": str(note.id),
        "text": note.text,
        "source": note.source,
        "created_at": note.created_at.isoformat() if note.created_at else None,
        "updated_at": note.updated_at.isoformat() if note.updated_at else None,
    }


def _writable(request: Request) -> None:
    if services(request).settings.lifeos_demo_mode:
        raise ApiError(403, "demo_account", "the shared demo's memory can't be changed")


@router.get("/memory")
async def memory(request: Request, principal: CurrentUser) -> dict[str, Any]:
    notes = await list_notes(services(request).session_factory, principal.user_id)
    return {
        "notes": [_view(n) for n in notes],
        "limits": {"notes": MAX_NOTES, "chars": MAX_CHARS},
        "editable": not services(request).settings.lifeos_demo_mode,
    }


@router.post("/memory", status_code=201)
async def remember(body: NoteBody, request: Request, principal: CurrentUser) -> dict[str, Any]:
    _writable(request)
    try:
        note = await add_note(
            services(request).session_factory, principal.user_id, body.text, body.source
        )
    except ValueError as exc:
        raise ApiError(422, "invalid_request", str(exc)) from exc
    except MemoryFull as exc:
        raise ApiError(409, "memory_full", f"memory holds at most {MAX_NOTES} notes") from exc
    return _view(note)


@router.put("/memory/{note_id}")
async def edit(
    note_id: uuid.UUID, body: EditBody, request: Request, principal: CurrentUser
) -> dict[str, Any]:
    _writable(request)
    try:
        note = await edit_note(
            services(request).session_factory, principal.user_id, note_id, body.text
        )
    except ValueError as exc:
        raise ApiError(422, "invalid_request", str(exc)) from exc
    if note is None:
        raise ApiError(404, "unknown_note", "no such note")
    return _view(note)


@router.delete("/memory/{note_id}", status_code=204)
async def forget(note_id: uuid.UUID, request: Request, principal: CurrentUser) -> Response:
    _writable(request)
    if not await delete_note(services(request).session_factory, principal.user_id, note_id):
        raise ApiError(404, "unknown_note", "no such note")
    return Response(status_code=204)
