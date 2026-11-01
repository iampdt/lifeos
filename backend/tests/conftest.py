"""Shared fixtures. Database tests use the `lifeos_test` database from docker-compose."""

from __future__ import annotations

import asyncio
import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from app.core.config import BACKEND_ROOT
from app.db.session import make_engine, make_session_factory

TEST_DATABASE_URL = os.environ.get(
    "LIFEOS_TEST_DATABASE_URL", "postgresql+asyncpg://lifeos:lifeos@localhost:5433/lifeos_test"
)


def _reachable(url: str) -> bool:
    async def probe() -> bool:
        engine = make_engine(url)
        try:
            async with engine.connect() as connection:
                await connection.execute(text("select 1"))
            return True
        except Exception:
            return False
        finally:
            await engine.dispose()

    return asyncio.run(probe())


@pytest.fixture(scope="session")
def migrated_database() -> str:
    """Rebuild the test schema from the migrations once per test session."""
    if not _reachable(TEST_DATABASE_URL):
        pytest.skip("test Postgres not reachable: run `docker compose up -d db`")
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    config.attributes["configure_logger"] = False
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    return TEST_DATABASE_URL


@pytest.fixture
async def session_factory(migrated_database):
    engine = make_engine(migrated_database)
    async with engine.begin() as connection:
        # TRUNCATE doesn't fire row-level triggers, so the append-only audit log can be reset.
        await connection.execute(
            text(
                "TRUNCATE action_executions, action_proposals, agent_runs, audit_log, "
                "sessions, users "
                "RESTART IDENTITY CASCADE"
            )
        )
    yield make_session_factory(engine)
    await engine.dispose()
