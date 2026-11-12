"""One process for the app and the public demo (LIFEOS_DEMO_MOUNT_DATABASE), and the in-process
tick (LIFEOS_TICK_MINUTES)."""

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy.engine import make_url

from app.api import cron
from app.api.main import create_app
from app.core.config import Settings

NEON = "postgresql://app:p%40ss@ep-cool-1.ap-southeast-1.aws.neon.tech/neondb?sslmode=require"
CREDENTIALS = (
    "notion_token",
    "lifeos_api_token",
    "lifeos_cron_secret",
    "google_client_id",
    "google_client_secret",
    "notion_oauth_client_id",
    "notion_oauth_client_secret",
    "lifeos_encryption_key",
)


def deployment() -> Settings:
    return Settings(
        _env_file=None,
        lifeos_env="production",
        database_url=NEON,
        notion_token="notion",
        gemini_api_key="gemini",
        lifeos_api_token="token",
        lifeos_cron_secret="cron",
        google_client_id="google-client",
        google_client_secret="google-secret",
        lifeos_public_url="https://lifeos.example",
        notion_oauth_client_id="notion-client",
        notion_oauth_client_secret="notion-secret",
        lifeos_encryption_key="key",
        lifeos_demo_mount_database="lifeos_demo",
        lifeos_tick_minutes=30,
    )


def test_the_mounted_demo_has_its_own_database_and_none_of_the_credentials():
    demo = deployment().mounted_demo()
    assert demo.lifeos_demo_mode
    url = make_url(demo.database_url)
    assert url.database == "lifeos_demo"
    assert url.host == "ep-cool-1.ap-southeast-1.aws.neon.tech" and url.password == "p@ss"
    assert url.query["sslmode"] == "require"
    assert demo.gemini_api_key is not None and demo.gemini_api_key.get_secret_value() == "gemini"
    assert demo.llm_daily_token_budget == 150_000
    assert all(getattr(demo, name) is None for name in CREDENTIALS)
    assert not (demo.google_sign_in or demo.notion_oauth or demo.password_sign_in)
    assert demo.lifeos_demo_mount_database is None and demo.lifeos_tick_minutes == 0


def test_nothing_is_mounted_unless_asked():
    assert create_app(settings=Settings(_env_file=None)).state.demo is None


async def test_requests_under_demo_reach_the_demo():
    app = create_app(settings=deployment())
    demo = app.state.demo
    assert demo is not None
    # The lifespan builds both apps' services; their settings are all /meta reads.
    app.state.services = SimpleNamespace(settings=app.state.settings)
    demo.state.services = SimpleNamespace(settings=demo.state.settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://api") as c:
        assert (await c.get("/api/v1/meta")).json()["demo"] is False
        assert (await c.get("/demo/api/v1/meta")).json() == {
            "demo": True,
            "google": False,
            "password": False,
            "contact": None,
        }
        assert (await c.get("/demo/api/v1/health")).json() == {"status": "ok"}


async def test_a_demo_that_did_not_start_answers_503():
    app = create_app(settings=deployment())
    app.state.services = SimpleNamespace(settings=app.state.settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://api") as c:
        response = await c.get("/demo/api/v1/meta")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "unavailable"
        assert (await c.get("/api/v1/meta")).status_code == 200


async def test_the_in_process_tick_outlives_a_failed_tick(monkeypatch):
    ticks: list[object] = []
    waits: list[float] = []

    async def tick(svc):
        ticks.append(svc)
        if len(ticks) == 1:
            raise RuntimeError("database unavailable")
        return {"status": "done"}

    async def sleep(seconds):
        waits.append(seconds)
        if len(waits) == 3:
            raise asyncio.CancelledError

    monkeypatch.setattr(cron, "_tick", tick)
    monkeypatch.setattr(asyncio, "sleep", sleep)
    with pytest.raises(asyncio.CancelledError):
        await cron.tick_forever("services", 30)
    assert ticks == ["services", "services"]
    assert all(5 < wait <= 30 * 60 + 5 for wait in waits)  # to the next :00 or :30, plus 5 s
