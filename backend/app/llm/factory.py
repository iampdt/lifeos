"""Build the LLM router from settings."""

from __future__ import annotations

from app.core.config import Settings
from app.llm.gemini import GeminiProvider
from app.llm.router import LLMRouter


def build_router(settings: Settings) -> LLMRouter:
    """Without GEMINI_API_KEY the router has no provider, and workflows use their fallbacks."""
    key = settings.gemini_api_key
    provider = GeminiProvider(key.get_secret_value()) if key else None
    return LLMRouter(
        provider,
        {"fast": settings.model_chain("fast"), "smart": settings.model_chain("smart")},
        timeout_seconds=settings.llm_timeout_seconds,
        daily_token_budget=settings.llm_daily_token_budget,
    )
