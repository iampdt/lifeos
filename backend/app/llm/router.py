"""Model chain with fallback, a daily token budget, and usage accounting.

Free tiers fail often (rate limits, "high demand" 503s), so each call walks a chain of pinned
models. If every model fails, the caller gets `LLMUnavailable` and must degrade gracefully.

Their speed also swings from day to day, so a call is hedged: when a model hasn't answered after
the tier's `hedge_seconds`, the next model in the chain starts alongside it and the first answer
wins. A healthy first model still answers every call; a slow one costs the wait, not a timeout.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from datetime import UTC, date, datetime

from pydantic import BaseModel

from app.llm.base import (
    Embeddings,
    EmbeddingTask,
    LLMBudgetExceeded,
    LLMError,
    LLMNotConfigured,
    LLMProvider,
    LLMResult,
    LLMUnavailable,
    LLMUsage,
)

logger = logging.getLogger("lifeos.llm")


class LLMRouter:
    def __init__(
        self,
        provider: LLMProvider | None,
        chains: dict[str, list[str]],
        *,
        timeout_seconds: float = 30.0,
        hedge_seconds: dict[str, float] | None = None,
        daily_token_budget: int = 300_000,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        embedding_model: str = "gemini-embedding-001",
    ) -> None:
        self.provider = provider
        self.chains = chains
        self.embedding_model = embedding_model
        self.timeout_seconds = timeout_seconds
        self.hedge_seconds = hedge_seconds or {}  # per tier; missing or 0: one model at a time
        self.daily_token_budget = daily_token_budget
        self.clock = clock
        self._spent: dict[date, int] = {}

    def tokens_spent_today(self) -> int:
        return self._spent.get(self.clock().date(), 0)

    async def generate[T: BaseModel](
        self,
        schema: type[T],
        *,
        system: str,
        prompt: str,
        tier: str = "fast",
        temperature: float = 0.2,
    ) -> LLMResult[T]:
        if self.provider is None:
            raise LLMNotConfigured("no LLM provider configured")
        if self.tokens_spent_today() >= self.daily_token_budget:
            raise LLMBudgetExceeded("daily LLM token budget reached")
        chain = self.chains.get(tier) or self.chains["fast"]
        hedge = self.hedge_seconds.get(tier) or None
        provider = self.provider
        failures: list[str] = []
        # Each running call, with its position in the chain and when it started.
        running: dict[asyncio.Future, tuple[int, float]] = {}

        def start(position: int) -> None:
            call = provider.generate_structured(
                model=chain[position],
                system=system,
                prompt=prompt,
                schema=schema,
                temperature=temperature,
                timeout_seconds=self.timeout_seconds,
            )
            running[asyncio.ensure_future(call)] = (position, time.monotonic())

        start(0)
        started = 1
        try:
            while running:
                more = started < len(chain)
                done, _ = await asyncio.wait(
                    running,
                    timeout=hedge if more else None,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not done:  # still thinking: start the next model alongside
                    logger.info("llm: no answer yet; also asking %s", chain[started])
                    start(started)
                    started += 1
                    continue
                for call in done:
                    position, began = running.pop(call)
                    try:
                        completion = call.result()
                    except LLMError as exc:
                        failures.append(str(exc))
                        logger.warning("llm %s failed: %s", chain[position], exc)
                        if not exc.fallback:
                            raise
                        continue
                    today = self.clock().date()
                    used = completion.input_tokens + completion.output_tokens
                    self._spent[today] = self._spent.get(today, 0) + used
                    return LLMResult(
                        value=completion.value,
                        usage=LLMUsage(
                            provider=provider.name,
                            model=chain[position],
                            input_tokens=completion.input_tokens,
                            output_tokens=completion.output_tokens,
                            latency_ms=int((time.monotonic() - began) * 1000),
                            fallbacks=position,
                        ),
                    )
                if not running and started < len(chain):  # every running model failed
                    start(started)
                    started += 1
        finally:
            for call in running:
                call.cancel()  # the answer is in, or the call failed
        raise LLMUnavailable("all models failed: " + " | ".join(failures))

    async def embed(self, texts: list[str], *, task: EmbeddingTask) -> Embeddings:
        """One model, no chain: vectors from different models can't be compared. The API
        reports no token counts for embeddings, so about four characters count as a token."""
        if self.provider is None:
            raise LLMNotConfigured("no LLM provider configured")
        if self.tokens_spent_today() >= self.daily_token_budget:
            raise LLMBudgetExceeded("daily LLM token budget reached")
        started = time.monotonic()
        vectors = await self.provider.embed(
            model=self.embedding_model, texts=texts, task=task,
            timeout_seconds=self.timeout_seconds,
        )  # fmt: skip
        tokens = sum(len(t) for t in texts) // 4 + 1
        today = self.clock().date()
        self._spent[today] = self._spent.get(today, 0) + tokens
        usage = LLMUsage(
            provider=self.provider.name, model=self.embedding_model, input_tokens=tokens,
            output_tokens=0, latency_ms=int((time.monotonic() - started) * 1000), fallbacks=0,
        )  # fmt: skip
        return Embeddings(vectors, usage)
