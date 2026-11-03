"""Deterministic provider for tests: scripted responses per output schema."""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict, deque
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from app.llm.base import EMBEDDING_DIMENSIONS, Completion, EmbeddingTask, normalise

Response = BaseModel | dict[str, Any] | Exception | Callable[[str], Any]


class MockLLM:
    name = "mock"

    def __init__(self, responses: dict[str, list[Response]] | None = None) -> None:
        self._queues: dict[str, deque[Response]] = defaultdict(deque)
        for schema_name, items in (responses or {}).items():
            self._queues[schema_name].extend(items)
        self.calls: list[dict[str, Any]] = []
        self.embed_error: Exception | None = None

    def queue(self, schema: type[BaseModel] | str, *items: Response) -> None:
        name = schema if isinstance(schema, str) else schema.__name__
        self._queues[name].extend(items)

    async def generate_structured[T: BaseModel](
        self,
        *,
        model: str,
        system: str,
        prompt: str,
        schema: type[T],
        temperature: float,
        timeout_seconds: float,
    ) -> Completion[T]:
        self.calls.append({"model": model, "schema": schema.__name__, "prompt": prompt})
        queue = self._queues[schema.__name__]
        if not queue:
            raise AssertionError(f"MockLLM has no response queued for {schema.__name__}")
        item = queue.popleft()
        if isinstance(item, Exception):
            raise item
        if callable(item) and not isinstance(item, BaseModel):
            item = item(prompt)
        value = item if isinstance(item, schema) else schema.model_validate(item)
        return Completion(value=value, input_tokens=len(prompt) // 4, output_tokens=20)

    async def embed(
        self, *, model: str, texts: list[str], task: EmbeddingTask, timeout_seconds: float
    ) -> list[list[float]]:
        """Bag-of-words vectors: texts that share words are close. `embed_error` fails a call."""
        self.calls.append({"model": model, "schema": "embed", "texts": list(texts), "task": task})
        if self.embed_error is not None:
            raise self.embed_error
        return [_bag_of_words(text) for text in texts]


def _bag_of_words(text: str) -> list[float]:
    vector = [0.0] * EMBEDDING_DIMENSIONS
    for word in re.findall(r"[a-z0-9]{3,}", text.lower()):
        digest = hashlib.blake2b(word.encode(), digest_size=4).digest()
        vector[int.from_bytes(digest) % EMBEDDING_DIMENSIONS] += 1.0
    return normalise(vector)
