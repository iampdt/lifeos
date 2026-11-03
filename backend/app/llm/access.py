"""Which key an account's model calls use (multi-user §AI under free tier).

For every call, in order:
1. the account's own Gemini key, if it added one Google accepts;
2. otherwise the service's key, if the account agreed to the free-tier terms (below) and has
   allowance left today (`LLM_USER_DAILY_TOKENS`, counted per UTC day in `ai_usage`), within
   the service's overall daily budget;
3. otherwise no model: the workflows use their rule-based fallbacks, and the reason reaches
   the user.

If the own key is refused or out of quota, the call falls back to step 2. The deployment owner
isn't routed here: it uses the service's key directly.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.crypto import TokenCipher
from app.db.models import AIAccess, AIUsage
from app.execution.audit import audit
from app.llm.base import (
    Embeddings,
    EmbeddingTask,
    LLMKeyRejected,
    LLMOff,
    LLMResult,
    LLMUnavailable,
)
from app.llm.router import LLMRouter

DISCLOSURE_VERSION = "2026-11-03"
DISCLOSURE = (
    "LifeOS uses Google's Gemini API on its free tier. On the free tier, Google may use what "
    "LifeOS sends (your check-in messages, task titles and other workspace content, and your "
    "saved notes) to improve its products, and human reviewers may read it. Turn AI on only if "
    "you're comfortable with that. Without AI, LifeOS still runs your routines with rules. With "
    "your own Gemini key, your calls follow the terms of your own Google account instead."
)


@dataclass(frozen=True)
class AIStatus:
    service_available: bool
    consented: bool
    consent_current: bool
    allowance: int
    used_today: int
    own_tokens_today: int
    own_keys: bool  # whether this deployment can store keys
    key_hint: str | None
    key_status: str | None

    @property
    def mode(self) -> str:
        if self.key_status == "active":
            return "own_key"
        if self.service_available and self.consent_current and self.used_today < self.allowance:
            return "allowance"
        if self.service_available and self.consent_current:
            return "allowance_used"
        return "off"


class AIAccessService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        service: LLMRouter,
        *,
        allowance: int,
        cipher: TokenCipher | None,
        own_router: Callable[[str], LLMRouter],
        check_key: Callable[[str], Awaitable[None]],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.session_factory = session_factory
        self.service = service
        self.allowance = allowance
        self.cipher = cipher
        self._own_router = own_router
        self._check_key = check_key
        self.clock = clock
        self._routers: dict[uuid.UUID, tuple[str, LLMRouter]] = {}  # user → (hint, router)

    def _today(self) -> date:
        return self.clock().astimezone(UTC).date()

    async def _rows(self, user_id: uuid.UUID) -> tuple[AIAccess | None, AIUsage | None]:
        async with self.session_factory() as db:
            access = await db.get(AIAccess, user_id)
            usage = await db.get(AIUsage, (user_id, self._today()))
        return access, usage

    async def status(self, user_id: uuid.UUID) -> AIStatus:
        access, usage = await self._rows(user_id)
        return AIStatus(
            service_available=self.service.provider is not None,
            consented=bool(access and access.consented_at),
            consent_current=bool(access and access.consent_version == DISCLOSURE_VERSION),
            allowance=self.allowance,
            used_today=usage.service_tokens if usage else 0,
            own_tokens_today=usage.own_tokens if usage else 0,
            own_keys=self.cipher is not None,
            key_hint=access.key_hint if access and access.key else None,
            key_status=access.key_status if access and access.key else None,
        )

    # --- routing ---------------------------------------------------------------------------

    async def own_router(self, user_id: uuid.UUID) -> LLMRouter | None:
        access, _ = await self._rows(user_id)
        if not (self.cipher and access and access.key and access.key_status == "active"):
            return None
        cached = self._routers.get(user_id)
        if cached and cached[0] == access.key_hint:
            return cached[1]
        router = self._own_router(self.cipher.decrypt(access.key))
        self._routers[user_id] = (access.key_hint or "", router)
        return router

    async def service_route(self, user_id: uuid.UUID) -> LLMRouter:
        """The service's router, or LLMOff saying why this account can't use it now."""
        status = await self.status(user_id)
        if not status.service_available:
            raise LLMOff("AI isn't set up on this deployment")
        if not status.consent_current:
            raise LLMOff("AI is off for your account: turn it on in Settings or add your own key")
        if status.used_today >= self.allowance:
            raise LLMOff("you've used today's AI allowance; add your own Gemini key for more")
        return self.service

    async def record(self, user_id: uuid.UUID, *, own: bool, tokens: int) -> None:
        column = "own_tokens" if own else "service_tokens"
        counts = {"service_tokens": 0, "own_tokens": 0, column: tokens}
        statement = insert(AIUsage).values(user_id=user_id, day=self._today(), **counts)
        async with self.session_factory() as db, db.begin():
            await db.execute(
                statement.on_conflict_do_update(
                    index_elements=["user_id", "day"],
                    set_={column: getattr(AIUsage, column) + tokens},
                )
            )

    async def key_rejected(self, user_id: uuid.UUID) -> None:
        self._routers.pop(user_id, None)
        async with self.session_factory() as db, db.begin():
            access = await db.get(AIAccess, user_id)
            if access and access.key:
                access.key_status = "rejected"
                audit(db, actor="system", event="ai.key_rejected", user_id=user_id)

    # --- account choices -------------------------------------------------------------------

    async def _update(self, user_id: uuid.UUID, event: str, **values: Any) -> None:
        async with self.session_factory() as db, db.begin():
            await db.execute(
                insert(AIAccess)
                .values(user_id=user_id, **values)
                .on_conflict_do_update(index_elements=["user_id"], set_=values)
            )
            audit(db, actor="user", event=event, user_id=user_id)

    async def consent(self, user_id: uuid.UUID) -> None:
        await self._update(user_id, "ai.consented", consented_at=self.clock(),
                           consent_version=DISCLOSURE_VERSION)  # fmt: skip

    async def withdraw(self, user_id: uuid.UUID) -> None:
        await self._update(user_id, "ai.consent_withdrawn", consented_at=None,
                           consent_version=None)  # fmt: skip

    async def set_key(self, user_id: uuid.UUID, key: str) -> str:
        """Checks the key with Google, then stores it encrypted. Returns its hint."""
        assert self.cipher is not None
        await self._check_key(key)  # LLMKeyRejected or LLMUnavailable propagate
        hint = key[-4:]
        await self._update(user_id, "ai.key_added", key=self.cipher.encrypt(key), key_hint=hint,
                           key_status="active", key_added_at=self.clock())  # fmt: skip
        self._routers.pop(user_id, None)
        return hint

    async def remove_key(self, user_id: uuid.UUID) -> None:
        await self._update(user_id, "ai.key_removed", key=None, key_hint=None, key_status=None,
                           key_added_at=None)  # fmt: skip
        self._routers.pop(user_id, None)


class AccountLLM:
    """The model router an account's workflows use; same interface as `LLMRouter`."""

    def __init__(self, user_id: uuid.UUID, access: AIAccessService) -> None:
        self.user_id = user_id
        self.access = access
        service = access.service
        self.provider = service.provider
        self.chains = service.chains
        self.daily_token_budget = access.allowance

    def tokens_spent_today(self) -> int:
        return self.access.service.tokens_spent_today()

    async def generate[T: BaseModel](
        self,
        schema: type[T],
        *,
        system: str,
        prompt: str,
        tier: str = "fast",
        temperature: float = 0.2,
    ) -> LLMResult[T]:
        kwargs = {"system": system, "prompt": prompt, "tier": tier, "temperature": temperature}
        own = await self.access.own_router(self.user_id)
        if own is not None:
            try:
                result = await own.generate(schema, **kwargs)
            except LLMKeyRejected:
                await self.access.key_rejected(self.user_id)
            except LLMUnavailable:
                pass  # the own key is out of quota or Google is struggling: try the allowance
            else:
                await self._record(result, own=True)
                return result
        service = await self.access.service_route(self.user_id)
        result = await service.generate(schema, **kwargs)
        await self._record(result, own=False)
        return result

    async def embed(self, texts: list[str], *, task: EmbeddingTask) -> Embeddings:
        """Like `generate`: the account's own key first, then the allowance. A vector from
        either key comes from the same model, so they can be compared."""
        own = await self.access.own_router(self.user_id)
        if own is not None:
            try:
                result = await own.embed(texts, task=task)
            except LLMKeyRejected:
                await self.access.key_rejected(self.user_id)
            except LLMUnavailable:
                pass
            else:
                await self.access.record(self.user_id, own=True, tokens=result.usage.input_tokens)
                return result
        service = await self.access.service_route(self.user_id)
        result = await service.embed(texts, task=task)
        await self.access.record(self.user_id, own=False, tokens=result.usage.input_tokens)
        return result

    async def _record(self, result: LLMResult, *, own: bool) -> None:
        tokens = result.usage.input_tokens + result.usage.output_tokens
        await self.access.record(self.user_id, own=own, tokens=tokens)
