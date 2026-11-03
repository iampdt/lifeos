"""Gemini via the official `google-genai` SDK, with JSON output constrained to a Pydantic schema."""

from __future__ import annotations

import asyncio

import httpx
from google import genai
from google.genai import errors, types
from pydantic import BaseModel, ValidationError

from app.llm.base import (
    EMBEDDING_DIMENSIONS,
    Completion,
    EmbeddingTask,
    LLMBadOutput,
    LLMError,
    LLMKeyRejected,
    LLMUnavailable,
    normalise,
)


def _key_rejected(exc: errors.ClientError) -> bool:
    # An invalid key is a 400 whose message names the key; a disabled one is 401 or 403.
    return exc.code in (401, 403) or (exc.code == 400 and "api key" in str(exc).lower())


class GeminiProvider:
    name = "gemini"

    def __init__(self, api_key: str) -> None:
        self._client = genai.Client(api_key=api_key)

    async def check_key(self, model: str) -> None:
        """Raises LLMKeyRejected if Google refuses the key; a quota or outage isn't the key's
        fault and raises LLMUnavailable."""
        try:
            await asyncio.wait_for(self._client.aio.models.get(model=model), timeout=15)
        except errors.ClientError as exc:
            if _key_rejected(exc):
                raise LLMKeyRejected(f"{exc.code} {exc.status}") from exc
            if exc.code == 429:
                return  # the key works; it's just out of quota right now
            raise LLMUnavailable(f"{exc.code} {exc.status}") from exc
        except errors.ServerError as exc:
            raise LLMUnavailable(f"{exc.code} {exc.status}") from exc
        except (TimeoutError, httpx.TimeoutException, httpx.TransportError) as exc:
            raise LLMUnavailable(type(exc).__name__) from exc

    async def embed(
        self, *, model: str, texts: list[str], task: EmbeddingTask, timeout_seconds: float
    ) -> list[list[float]]:
        config = types.EmbedContentConfig(
            task_type="RETRIEVAL_DOCUMENT" if task == "document" else "RETRIEVAL_QUERY",
            output_dimensionality=EMBEDDING_DIMENSIONS,
            http_options=types.HttpOptions(timeout=int(timeout_seconds * 1000)),
        )
        try:
            response = await asyncio.wait_for(
                self._client.aio.models.embed_content(model=model, contents=texts, config=config),
                timeout=timeout_seconds + 5,
            )
        except errors.ClientError as exc:
            if exc.code in (404, 429):
                raise LLMUnavailable(f"{model}: {exc.code} {exc.status}") from exc
            if _key_rejected(exc):
                raise LLMKeyRejected(f"{model}: {exc.code} {exc.status}") from exc
            raise LLMError(f"{model}: {exc.code} {exc.status}") from exc
        except errors.ServerError as exc:
            raise LLMUnavailable(f"{model}: {exc.code} {exc.status}") from exc
        except (TimeoutError, httpx.TimeoutException, httpx.TransportError) as exc:
            raise LLMUnavailable(f"{model}: {type(exc).__name__}") from exc
        vectors = [list(e.values or []) for e in response.embeddings or []]
        if len(vectors) != len(texts) or any(len(v) != EMBEDDING_DIMENSIONS for v in vectors):
            raise LLMBadOutput(f"{model}: expected {len(texts)} embeddings")
        # Below full size the model's vectors aren't unit length; cosine search wants them so.
        return [normalise(v) for v in vectors]

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
        config = types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_schema=schema,
            temperature=temperature,
            http_options=types.HttpOptions(timeout=int(timeout_seconds * 1000)),
        )
        try:
            response = await asyncio.wait_for(
                self._client.aio.models.generate_content(
                    model=model, contents=prompt, config=config
                ),
                timeout=timeout_seconds + 5,
            )
        except errors.ClientError as exc:
            # 404: model not offered to this account; 429: quota. Both mean "try the next model".
            if exc.code in (404, 429):
                raise LLMUnavailable(f"{model}: {exc.code} {exc.status}") from exc
            if _key_rejected(exc):
                raise LLMKeyRejected(f"{model}: {exc.code} {exc.status}") from exc
            raise LLMError(f"{model}: {exc.code} {exc.status}") from exc
        except errors.ServerError as exc:  # 500/503/504
            raise LLMUnavailable(f"{model}: {exc.code} {exc.status}") from exc
        except (TimeoutError, httpx.TimeoutException, httpx.TransportError) as exc:
            raise LLMUnavailable(f"{model}: {type(exc).__name__}") from exc

        try:
            text = response.text or ""
            value = schema.model_validate_json(text)
        except (ValidationError, ValueError) as exc:
            raise LLMBadOutput(f"{model}: response didn't match {schema.__name__}") from exc
        usage = response.usage_metadata
        return Completion(
            value=value,
            input_tokens=(usage.prompt_token_count or 0) if usage else 0,
            output_tokens=(
                (usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0)
                if usage
                else 0
            ),
        )
