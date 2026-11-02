"""Append-only audit trail. Details hold IDs, kinds and outcomes, not page contents."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditLog


def audit(
    session: AsyncSession,
    *,
    actor: str,
    event: str,
    user_id: uuid.UUID | None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditLog(
            user_id=user_id,
            actor=actor,
            event=event,
            entity_type=entity_type,
            entity_id=entity_id,
            details=details or {},
        )
    )
