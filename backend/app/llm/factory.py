"""Build LLM routers from settings: the service's, the accounts' share of it, and one per
account key."""

from __future__ import annotations

from app.core.config import Settings
from app.llm.base import LLMProvider
from app.llm.gateway import GatewayProvider, RoutedProvider
from app.llm.gemini import GeminiProvider
from app.llm.router import LLMRouter

# An account's own key has no LifeOS budget; Google's quota for that key applies.
UNLIMITED = 10**12


def _chains(settings: Settings, *, gateway: bool) -> dict[str, list[str]]:
    return {
        "fast": settings.model_chain("fast", gateway=gateway),
        "smart": settings.model_chain("smart", gateway=gateway),
    }


def build_router(settings: Settings) -> LLMRouter:
    """Without GEMINI_API_KEY the router has no provider, and workflows use their fallbacks.
    With the gateway configured too, `tollgate:` chain entries go through it (decision I60)."""
    key = settings.gemini_api_key
    provider: LLMProvider | None = GeminiProvider(key.get_secret_value()) if key else None
    gateway = provider is not None and settings.gateway
    if gateway:
        assert settings.tollgate_url and settings.tollgate_api_key
        provider = RoutedProvider(
            provider,
            GatewayProvider(settings.tollgate_url, settings.tollgate_api_key.get_secret_value()),
        )
    return LLMRouter(
        provider,
        _chains(settings, gateway=gateway),
        timeout_seconds=settings.llm_timeout_seconds,
        hedge_seconds=settings.hedge_seconds,
        daily_token_budget=settings.llm_daily_token_budget,
        embedding_model=settings.llm_embedding_model,
    )


def accounts_router(settings: Settings, service: LLMRouter) -> LLMRouter:
    """The service router as other accounts use it: Gemini only, the provider named in their AI
    terms, counted against the same daily budget."""
    provider = service.provider
    if not isinstance(provider, RoutedProvider):
        return service
    return service.sibling(provider.default, _chains(settings, gateway=False))


def own_key_router(settings: Settings, api_key: str) -> LLMRouter:
    return LLMRouter(
        GeminiProvider(api_key),
        _chains(settings, gateway=False),
        timeout_seconds=settings.llm_timeout_seconds,
        hedge_seconds=settings.hedge_seconds,
        daily_token_budget=UNLIMITED,
        embedding_model=settings.llm_embedding_model,
    )


async def check_gemini_key(settings: Settings, api_key: str) -> None:
    await GeminiProvider(api_key).check_key(settings.model_chain("fast", gateway=False)[0])
