"""Model chain with fallback, a daily token budget, and usage accounting.

Free tiers fail often (rate limits, "high demand" 503s), so each call walks a chain of pinned
models. If every model fails, the caller gets `LLMUnavailable` and must degrade gracefully.
"""

from __future__ import annotations

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
        daily_token_budget: int = 300_000,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        embedding_model: str = "gemini-embedding-001",
    ) -> None:
        self.provider = provider
        self.chains = chains
        self.embedding_model = embedding_model
        self.timeout_seconds = timeout_seconds
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
        failures: list[str] = []
        for model in self.chains.get(tier) or self.chains["fast"]:
            started = time.monotonic()
            try:
                completion = await self.provider.generate_structured(
                    model=model,
                    system=system,
                    prompt=prompt,
                    schema=schema,
                    temperature=temperature,
                    timeout_seconds=self.timeout_seconds,
                )
            except LLMError as exc:
                failures.append(str(exc))
                logger.warning("llm %s failed: %s", model, exc)
                if not exc.fallback:
                    raise
                continue
            today = self.clock().date()
            used = completion.input_tokens + completion.output_tokens
            self._spent[today] = self._spent.get(today, 0) + used
            return LLMResult(
                value=completion.value,
                usage=LLMUsage(
                    provider=self.provider.name,
                    model=model,
                    input_tokens=completion.input_tokens,
                    output_tokens=completion.output_tokens,
                    latency_ms=int((time.monotonic() - started) * 1000),
                    fallbacks=len(failures),
                ),
            )
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
