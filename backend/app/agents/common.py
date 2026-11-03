"""Shared plumbing for specialist agents: one structured LLM call, never an exception.

An agent that can't reach a model returns no value and an error string; the caller then uses
its deterministic fallback (architecture §5.6).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from pydantic import BaseModel

from app.llm.base import LLMError
from app.llm.router import LLMRouter


@dataclass(frozen=True)
class AgentCall[T: BaseModel]:
    value: T | None
    usage: dict[str, Any] | None
    error: str | None


async def ask_model[T: BaseModel](
    llm: LLMRouter, schema: type[T], *, agent: str, system: str, prompt: str, tier: str = "fast"
) -> AgentCall[T]:
    try:
        result = await llm.generate(schema, system=system, prompt=prompt, tier=tier)
    except LLMError as exc:
        return AgentCall(None, None, f"{type(exc).__name__}: {exc}")
    return AgentCall(result.value, {"agent": agent, **asdict(result.usage)}, None)


def unavailable_warning(error: str | None) -> str:
    kind, _, detail = (error or "").partition(":")
    if kind == "LLMOff":  # the account's own situation, explained in the message
        return f"{detail.strip()}; used the rule-based fallback."
    return f"AI unavailable ({kind or 'unknown error'}); used the rule-based fallback."
