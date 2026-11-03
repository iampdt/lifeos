"""Provider-neutral LLM interface: structured output only.

Every call asks for JSON matching a Pydantic model, and the result is validated again in code.
Free-form text never drives an action.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel


class LLMError(Exception):
    """Base class. `fallback` says whether trying the next model in the chain could help."""

    fallback = True


class LLMUnavailable(LLMError):
    """Rate limited, overloaded, timed out, or the model isn't available to this account."""


class LLMBadOutput(LLMError):
    """The response didn't parse or didn't match the schema."""


class LLMBudgetExceeded(LLMError):
    fallback = False


class LLMNotConfigured(LLMError):
    fallback = False


class LLMOff(LLMError):
    """AI isn't available to this account right now: it hasn't agreed to the free-tier terms
    and has no key of its own, or today's allowance is used up. The message says which, for
    the user."""

    fallback = False


class LLMKeyRejected(LLMError):
    """The provider refused the API key itself (invalid, revoked or not permitted)."""

    fallback = False


@dataclass(frozen=True)
class Completion[T: BaseModel]:
    value: T
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class LLMUsage:
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    fallbacks: int  # how many models failed before this one answered


@dataclass(frozen=True)
class LLMResult[T: BaseModel]:
    value: T
    usage: LLMUsage


class LLMProvider(Protocol):
    name: str

    async def generate_structured[T: BaseModel](
        self,
        *,
        model: str,
        system: str,
        prompt: str,
        schema: type[T],
        temperature: float,
        timeout_seconds: float,
    ) -> Completion[T]: ...
