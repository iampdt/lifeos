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


@lru_cache
def get_settings() -> Settings:
    return Settings()


def env_values() -> dict[str, str]:
    """Process environment layered over `.env`, used to resolve data-source IDs in the mapping."""
    values = {k: v for k, v in dotenv_values(ENV_FILE).items() if v is not None}
    values.update(os.environ)
    return values
