"""Runtime settings, loaded from environment variables and the repo-root `.env`."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values
from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_ROOT.parent
ENV_FILE = REPO_ROOT / ".env"
# Chain entries starting with this go to the Tollgate gateway (app/llm/gateway.py).
GATEWAY_PREFIX = "tollgate:"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, env_file_encoding="utf-8", extra="ignore")

    # "production" hides the OpenAPI docs and always uses Secure cookies.
    lifeos_env: Literal["development", "production"] = "development"

    notion_token: SecretStr | None = None
    notion_version: str = "2025-09-03"
    notion_base_url: str = "https://api.notion.com/v1"
    # Notion documents an average of 3 requests/second per connection; stay below it.
    notion_requests_per_second: float = 2.5
    notion_mapping_path: Path = BACKEND_ROOT / "config" / "notion_mapping.yaml"
    # The environment's workspace: the real data sources ("live") or test copies ("sandbox").
    notion_profile: str = "live"
    timezone: str = "Asia/Kolkata"

    # Either a SQLAlchemy asyncpg URL or a plain libpq URL such as Neon's (see app/db/session.py).
    database_url: str = "postgresql+asyncpg://lifeos:lifeos@localhost:5433/lifeos"
    # Kill switch: no Notion write happens unless this is explicitly true (architecture §6.3).
    lifeos_writes_enabled: bool = False

    gemini_api_key: SecretStr | None = None
    # Pinned versions, tried in order. "fast" parses and plans; "smart" writes reviews.
    # Chosen 2026-11-01 from measured calls (docs/build-log/phase-4b.md). `tollgate:<route>`
    # entries go to the gateway and are skipped until TOLLGATE_URL and TOLLGATE_API_KEY are set
    # (decision I60).
    llm_fast_models: str = "gemini-3.5-flash-lite,tollgate:smart,gemini-3.1-flash-lite"
    llm_smart_models: str = "gemini-3.5-flash,tollgate:smart,gemini-3.5-flash-lite"
    # The gateway, used by the owner's workspace and the demo. Other accounts stay on Gemini
    # only, the provider their AI terms name.
    tollgate_url: str | None = None
    tollgate_api_key: SecretStr | None = None
    llm_timeout_seconds: float = 30.0
    # A model that hasn't answered after this long gets the next model in its chain started
    # alongside, and the first answer wins (0: one at a time). The same model has taken 1 s one
    # week and 6 to 21 s the next (decision I59). "smart" waits longer: its answers are long.
    llm_fast_hedge_seconds: float = 3.0
    llm_smart_hedge_seconds: float = 10.0
    llm_daily_token_budget: int = 300_000  # the service key, all accounts together
    # The knowledge index's embeddings (architecture §5.9). Changing either means re-indexing.
    llm_embedding_model: str = "gemini-embedding-001"
    # Each account's daily allowance on the service key (the owner isn't limited by it).
    llm_user_daily_tokens: int = 40_000

    # Bearer token for scripts; it acts as the owner account. Unset: bearer requests are refused.
    lifeos_api_token: SecretStr | None = None
    # Sign in with Google (OpenID Connect). LIFEOS_PUBLIC_URL is the web app's origin; Google
    # redirects to {LIFEOS_PUBLIC_URL}/api/v1/auth/google/callback.
    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None
    lifeos_public_url: str | None = None
    # New accounts from Google sign-in. While off, Google sign-in only links to accounts that
    # already exist (matched by email).
    lifeos_open_signup: bool = False
    # Shown on the privacy and terms pages as the way to reach whoever runs this deployment.
    lifeos_contact_email: str | None = None
    # Connect Notion (a public integration with OAuth). Notion redirects to
    # {LIFEOS_PUBLIC_URL}/api/v1/notion/callback. Stored tokens are encrypted with
    # LIFEOS_ENCRYPTION_KEY: one or more comma-separated Fernet keys, the first one encrypting.
    notion_oauth_client_id: str | None = None
    notion_oauth_client_secret: SecretStr | None = None
    lifeos_encryption_key: SecretStr | None = None

    # Owner login (Phase 6). Secure cookies need HTTPS; set false only for local http dev.
    lifeos_cookie_secure: bool = True
    lifeos_session_days: int = 14

    # Shared secret for HMAC-signed cron calls (`POST /internal/cron/{job}`). Unset disables them.
    lifeos_cron_secret: SecretStr | None = None
    # The owner's routine times, local to TIMEZONE (other accounts set their own).
    lifeos_morning_time: str = "07:30"
    lifeos_evening_time: str = "21:00"
    lifeos_review_weekday: int = 6  # Monday is 0
    lifeos_review_time: str = "18:00"
    # Each scheduled tick starts at most this many runs, and none after this many seconds.
    lifeos_tick_max_runs: int = 4
    lifeos_tick_seconds: float = 60.0
    # Also tick in this process every this many minutes, on the clock (0: off). It only ticks
    # while the instance is awake, so on a free host something must keep it awake (deployment.md).
    lifeos_tick_minutes: int = 0

    # Public demo: an in-memory sample workspace instead of Notion, and a "Try the demo" sign-in.
    # Requires a database whose name contains "demo", because the demo resets it.
    lifeos_demo_mode: bool = False
    # Use this database name on the server in DATABASE_URL. Lets the demo reuse the main
    # service's connection string (copied by the Blueprint) while pointing at its own database.
    lifeos_demo_database: str | None = None
    # Also serve the public demo from this process, under /demo, on this database of the same
    # server: one free instance kept awake then hosts both (deployment.md "Public demo").
    lifeos_demo_mount_database: str | None = None
    # That demo's daily token budget: anyone on the internet can use its chat.
    lifeos_demo_daily_tokens: int = 150_000

    @model_validator(mode="after")
    def _production_is_strict(self) -> Settings:
        if self.lifeos_env == "production":
            self.lifeos_cookie_secure = True
        return self

    @model_validator(mode="after")
    def _demo_database(self) -> Settings:
        # Applied here, so the app and the migrations (alembic/env.py) see the same database.
        if self.lifeos_demo_database:
            from app.db.session import with_database

            self.database_url = with_database(self.database_url, self.lifeos_demo_database)
        return self

    @property
    def production(self) -> bool:
        return self.lifeos_env == "production"

    @property
    def google_sign_in(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret and self.lifeos_public_url)

    @property
    def notion_oauth(self) -> bool:
        return bool(
            self.notion_oauth_client_id
            and self.notion_oauth_client_secret
            and self.lifeos_encryption_key
            and self.lifeos_public_url
            and not self.lifeos_demo_mode
        )

    @property
    def password_sign_in(self) -> bool:
        """Passwords are a fallback for deployments without Google sign-in (local development)."""
        return not self.google_sign_in and not self.lifeos_demo_mode

    def mounted_demo(self) -> Settings:
        """Settings for the demo served under /demo: this server's demo database and the same
        models, but none of this deployment's Notion, sign-in or job credentials."""
        from app.db.session import with_database

        assert self.lifeos_demo_mount_database
        return self.model_copy(
            update={
                "lifeos_demo_mode": True,
                "database_url": with_database(self.database_url, self.lifeos_demo_mount_database),
                "llm_daily_token_budget": self.lifeos_demo_daily_tokens,
                "lifeos_demo_mount_database": None,
                "lifeos_tick_minutes": 0,
                "notion_token": None,
                "lifeos_api_token": None,
                "lifeos_cron_secret": None,
                "google_client_id": None,
                "google_client_secret": None,
                "notion_oauth_client_id": None,
                "notion_oauth_client_secret": None,
                "lifeos_encryption_key": None,
            }
        )

    @property
    def hedge_seconds(self) -> dict[str, float]:
        return {"fast": self.llm_fast_hedge_seconds, "smart": self.llm_smart_hedge_seconds}

    @property
    def gateway(self) -> bool:
        return bool(self.tollgate_url and self.tollgate_api_key)

    def model_chain(self, tier: str, *, gateway: bool = True) -> list[str]:
        """The tier's models in order; gateway entries only when the gateway is configured and
        the caller allows it."""
        raw = self.llm_smart_models if tier == "smart" else self.llm_fast_models
        names = [name.strip() for name in raw.split(",") if name.strip()]
        keep_gateway = gateway and self.gateway
        return [n for n in names if keep_gateway or not n.startswith(GATEWAY_PREFIX)]

    @property
    def checkpoint_url(self) -> str:
        # LangGraph's Postgres checkpointer uses psycopg (libpq URL form), not asyncpg.
        from app.db.session import libpq_url

        return libpq_url(self.database_url)


@lru_cache
def get_settings() -> Settings:
    return Settings()


def env_values() -> dict[str, str]:
    """Process environment layered over `.env`, used to resolve data-source IDs in the mapping."""
    values = {k: v for k, v in dotenv_values(ENV_FILE).items() if v is not None}
    values.update(os.environ)
    return values
