"""Tollgate, an OpenAI-compatible LLM gateway, as a second route for model calls.

Gemini's free tier swings between fast, slow and "high demand" 503s (decision I59). The gateway
fronts other free providers (Groq first) behind one key with its own fallbacks, so a chain entry
such as `tollgate:smart` gives the router somewhere else to go when Gemini struggles.

The gateway forwards plain chat messages only, so the schema goes into the system prompt and
the reply is validated in code like every other answer. Embeddings always stay on Gemini:
vectors from different models can't be compared.
"""

from __future__ import annotations

import json
import re

import httpx
from pydantic import BaseModel, ValidationError

from app.core.config import GATEWAY_PREFIX
from app.llm.base import (
    Completion,
    EmbeddingTask,
    LLMBadOutput,
    LLMError,
    LLMProvider,
    LLMUnavailable,
)

MAX_OUTPUT_TOKENS = 4096
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")


def json_instructions(schema: type[BaseModel]) -> str:
    return (
        "Reply with exactly one JSON object and nothing else: no prose, no code fences. "
        "It must validate against this JSON Schema:\n"
        + json.dumps(schema.model_json_schema(), separators=(",", ":"))
    )


def extract_json(text: str) -> str:
    """The object in a reply, tolerating code fences or a sentence around it."""
    text = _FENCE.sub("", text.strip())
    start, end = text.find("{"), text.rfind("}")
    return text[start : end + 1] if start != -1 and end > start else text


class GatewayProvider:
    name = "tollgate"

    def __init__(
        self, base_url: str, api_key: str, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            # Workspace content must not sit in the gateway's response cache.
            headers={"Authorization": f"Bearer {api_key}", "X-Tollgate-Cache": "off"},
            transport=transport,
        )

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
        route = model.removeprefix(GATEWAY_PREFIX)
        body = {
            "model": route,
            "messages": [
                {"role": "system", "content": f"{system}\n\n{json_instructions(schema)}"},
                {"role": "user", "content": prompt},
            ],
            "temperature": temperature,
            "max_tokens": MAX_OUTPUT_TOKENS,
        }
        try:
            response = await self._client.post(
                "/v1/chat/completions", json=body, timeout=timeout_seconds
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise LLMUnavailable(f"{model}: {type(exc).__name__}") from exc
        if response.status_code != 200:
            code = _error_code(response)
            # A refused gateway key is the deployment's problem, not the caller's: the chain
            # should move on to the next model rather than stop, so it isn't LLMKeyRejected.
            if response.status_code in (401, 403, 429) or response.status_code >= 500:
                raise LLMUnavailable(f"{model}: {response.status_code} {code}")
            raise LLMError(f"{model}: {response.status_code} {code}")
        try:
            data = response.json()
            text = data["choices"][0]["message"]["content"] or ""
            value = schema.model_validate_json(extract_json(text))
        except (ValidationError, ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMBadOutput(f"{model}: response didn't match {schema.__name__}") from exc
        usage = data.get("usage") or {}
        return Completion(
            value=value,
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
        )

    async def embed(
        self, *, model: str, texts: list[str], task: EmbeddingTask, timeout_seconds: float
    ) -> list[list[float]]:
        raise LLMError("the gateway doesn't serve embeddings")

    async def aclose(self) -> None:
        await self._client.aclose()


def _error_code(response: httpx.Response) -> str:
    try:
        return str(response.json()["error"]["code"])
    except (ValueError, KeyError, TypeError):
        return response.reason_phrase


class RoutedProvider:
    """Sends `tollgate:` models to the gateway and every other model, and all embeddings, to
    the default provider."""

    def __init__(self, default: LLMProvider, gateway: LLMProvider) -> None:
        self.default = default
        self.gateway = gateway
        self.name = default.name

    def name_for(self, model: str) -> str:
        return self._pick(model).name

    def _pick(self, model: str) -> LLMProvider:
        return self.gateway if model.startswith(GATEWAY_PREFIX) else self.default

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
        return await self._pick(model).generate_structured(
            model=model, system=system, prompt=prompt, schema=schema,
            temperature=temperature, timeout_seconds=timeout_seconds,
        )  # fmt: skip

    async def embed(
        self, *, model: str, texts: list[str], task: EmbeddingTask, timeout_seconds: float
    ) -> list[list[float]]:
        return await self.default.embed(
            model=model, texts=texts, task=task, timeout_seconds=timeout_seconds
        )
