"""Shared fixtures. Database tests use the `lifeos_test` database from docker-compose, which
starts each test with one account: the owner."""

from __future__ import annotations

import asyncio
import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from app.core.config import BACKEND_ROOT
from app.db.session import make_engine, make_session_factory
from tests.workflow_support import OWNER_EMAIL, OWNER_ID

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


def _clear_data(url: str) -> None:
    """Empty every table. Data left by the last run (several accounts' rows, say) doesn't fit
    older schemas, and the downgrades below should run on an empty database."""

    async def clear() -> None:
        engine = make_engine(url)
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        """
                        DO $$
                        DECLARE t text;
                        BEGIN
                            FOR t IN SELECT tablename FROM pg_tables
                                     WHERE schemaname = 'public' AND tablename <> 'alembic_version'
                            LOOP
                                EXECUTE format('TRUNCATE %I CASCADE', t);
                            END LOOP;
                        END $$;
                        """
                    )
                )
        finally:
            await engine.dispose()

    asyncio.run(clear())


@pytest.fixture(scope="session")
def migrated_database() -> str:
    """Rebuild the test schema from the migrations once per test session."""
    if not _reachable(TEST_DATABASE_URL):
        pytest.skip("test Postgres not reachable: run `docker compose up -d db`")
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    config.attributes["configure_logger"] = False
    _clear_data(TEST_DATABASE_URL)
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
                "TRUNCATE action_executions, action_proposals, cron_runs, agent_runs, "
                "audit_log, sessions, oauth_states, notion_connections, workspaces, ai_access, "
                "ai_usage, account_settings, memory_facts, knowledge_chunks, knowledge_pages, "
                "users "
                "RESTART IDENTITY CASCADE"
            )
        )
        await connection.execute(
            text("INSERT INTO users (id, email, is_owner) VALUES (:id, :email, true)"),
            {"id": OWNER_ID, "email": OWNER_EMAIL},
        )
    yield make_session_factory(engine)
    await engine.dispose()
