"""Append-only audit trail. Details hold IDs, kinds and outcomes, not page contents."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditLog


def audit(
    session: AsyncSession,
    *,
    actor: str,
    event: str,
    entity_type: str | None = None,
    entity_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditLog(
            actor=actor,
            event=event,
            entity_type=entity_type,
            entity_id=entity_id,
            details=details or {},
        )
    )
