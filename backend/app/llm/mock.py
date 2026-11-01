"""Deterministic provider for tests: scripted responses per output schema."""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from app.llm.base import Completion

Response = BaseModel | dict[str, Any] | Exception | Callable[[str], Any]


class MockLLM:
    name = "mock"

    def __init__(self, responses: dict[str, list[Response]] | None = None) -> None:
        self._queues: dict[str, deque[Response]] = defaultdict(deque)
        for schema_name, items in (responses or {}).items():
            self._queues[schema_name].extend(items)
        self.calls: list[dict[str, Any]] = []

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
