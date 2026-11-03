"""Build LLM routers from settings: the service's, and one per account key."""

from __future__ import annotations

from app.core.config import Settings
from app.llm.gemini import GeminiProvider
from app.llm.router import LLMRouter

# An account's own key has no LifeOS budget; Google's quota for that key applies.
UNLIMITED = 10**12


def build_router(settings: Settings) -> LLMRouter:
    """Without GEMINI_API_KEY the router has no provider, and workflows use their fallbacks."""
    key = settings.gemini_api_key
    provider = GeminiProvider(key.get_secret_value()) if key else None
    return LLMRouter(
        provider,
        {"fast": settings.model_chain("fast"), "smart": settings.model_chain("smart")},
        timeout_seconds=settings.llm_timeout_seconds,
        daily_token_budget=settings.llm_daily_token_budget,
        embedding_model=settings.llm_embedding_model,
    )


def own_key_router(settings: Settings, api_key: str) -> LLMRouter:
    return LLMRouter(
        GeminiProvider(api_key),
        {"fast": settings.model_chain("fast"), "smart": settings.model_chain("smart")},
        timeout_seconds=settings.llm_timeout_seconds,
        daily_token_budget=UNLIMITED,
        embedding_model=settings.llm_embedding_model,
    )


async def check_gemini_key(settings: Settings, api_key: str) -> None:
    await GeminiProvider(api_key).check_key(settings.model_chain("fast")[0])
