"""Runtime settings, loaded from environment variables and the repo-root `.env`."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import dotenv_values
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_ROOT.parent
ENV_FILE = REPO_ROOT / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, env_file_encoding="utf-8", extra="ignore")

    notion_token: SecretStr | None = None
    notion_version: str = "2025-09-03"
    notion_base_url: str = "https://api.notion.com/v1"
    # Notion documents an average of 3 requests/second per connection; stay below it.
    notion_requests_per_second: float = 2.5
    notion_mapping_path: Path = BACKEND_ROOT / "config" / "notion_mapping.yaml"
    # Which set of data sources to use: the real Life OS ("live") or the test copies ("sandbox").
    notion_profile: str = "live"
    timezone: str = "Asia/Kolkata"

    database_url: str = "postgresql+asyncpg://lifeos:lifeos@localhost:5433/lifeos"
    # Global kill switch: no Notion write happens unless this is explicitly true (design §9.3).
    lifeos_writes_enabled: bool = False

    gemini_api_key: SecretStr | None = None
    # Pinned versions, tried in order. "fast" parses and plans; "smart" writes reviews.
    # Chosen 2026-11-01 from measured calls (docs/progress/phase-4b.md).
    llm_fast_models: str = "gemini-3.5-flash-lite,gemini-3.1-flash-lite"
    llm_smart_models: str = "gemini-3.5-flash,gemini-3.5-flash-lite"
    llm_timeout_seconds: float = 30.0
    llm_daily_token_budget: int = 300_000

    # Bearer token for the API until session auth arrives with the frontend (Phase 6).
    # Unset means every API route refuses requests (fail closed).
    lifeos_api_token: SecretStr | None = None

    def model_chain(self, tier: str) -> list[str]:
        raw = self.llm_smart_models if tier == "smart" else self.llm_fast_models
        return [name.strip() for name in raw.split(",") if name.strip()]

    @property
    def checkpoint_url(self) -> str:
        # LangGraph's Postgres checkpointer uses psycopg, not SQLAlchemy's asyncpg URL form.
        return self.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


@lru_cache
def get_settings() -> Settings:
    return Settings()


def env_values() -> dict[str, str]:
    """Process environment layered over `.env`, used to resolve data-source IDs in the mapping."""
    values = {k: v for k, v in dotenv_values(ENV_FILE).items() if v is not None}
    values.update(os.environ)
    return values
