"""Build and reset the public demo (LIFEOS_DEMO_MODE=true).

The demo runs the real application (workflows, agents, approvals, executor, chat) on an
in-memory sample workspace instead of Notion. Visitors share one workspace, so it is rebuilt
whenever the service starts (the free host restarts it after an idle spell) and on request.
Because a reset wipes the database tables, the demo refuses to run against a database whose
name doesn't contain "demo".
"""

from __future__ import annotations

import secrets

from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.passwords import hash_password
from app.db.models import User
from app.db.session import async_url
from app.demo.notion import InMemoryNotion
from app.demo.seed import DEMO_ENV, seed_workspace
from app.notion.mapping import DataSourceSpec, load_mapping
from app.workflows.deps import WorkflowDeps

DEMO_EMAIL = "demo@example.com"
DEMO_IDENTITY = {"object": "user", "name": "LifeOS demo",
                 "bot": {"workspace_name": "Sample workspace (in memory)"}}  # fmt: skip
RESET_TABLES = "action_executions, action_proposals, cron_runs, agent_runs, audit_log"


class NotADemoDatabase(RuntimeError):
    pass


def ensure_demo_database(database_url: str) -> None:
    name = make_url(async_url(database_url)).database or ""
    if "demo" not in name.lower():
        raise NotADemoDatabase(
            f"demo mode resets its database; refusing to use {name!r} (name must contain 'demo')"
        )


def demo_specs(mapping_path) -> dict[str, DataSourceSpec]:
    return load_mapping(mapping_path, DEMO_ENV, "live")


def demo_notion() -> InMemoryNotion:
    return InMemoryNotion(identity=DEMO_IDENTITY)


async def reset_demo(deps: WorkflowDeps) -> None:
    """Fresh sample workspace, and no runs, proposals or audit entries from earlier visitors."""
    seed_workspace(deps.notion, deps.specs, deps.today())
    async with deps.session_factory() as db, db.begin():
        await db.execute(text(f"TRUNCATE {RESET_TABLES} RESTART IDENTITY CASCADE"))


async def demo_user(session_factory: async_sessionmaker[AsyncSession]) -> User:
    """The shared demo account. Its password is random and never used: visitors get a session
    through `POST /auth/demo`."""
    async with session_factory() as db, db.begin():
        user = await db.scalar(select(User).where(User.email == DEMO_EMAIL))
        if user is None:
            user = User(email=DEMO_EMAIL, password_hash=hash_password(secrets.token_urlsafe(32)))
            db.add(user)
            await db.flush()
        return user
