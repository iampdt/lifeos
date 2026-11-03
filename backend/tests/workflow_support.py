"""Shared setup for workflow and API tests: FakeNotion + MockLLM + in-memory checkpointer.

Every test database starts with one account, the owner (`OWNER_ID`), created by the
`session_factory` fixture.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from functools import partial
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import update

from app.api.main import Services
from app.auth.passwords import hash_password
from app.core.config import Settings
from app.db.models import User
from app.domain.models import Task
from app.llm.mock import MockLLM
from app.llm.router import LLMRouter
from app.notion.codecs import DateValue, RelationValue, decode_property
from app.permissions.modes import load_modes
from app.workflows.contexts import Contexts
from app.workflows.deps import WorkflowDeps
from app.workflows.runner import WorkflowRunner
from tests.factories import specs
from tests.fake_notion import FakeNotion

SPECS = specs()
OWNER_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")
OWNER_EMAIL = "owner@example.com"


def router(mock: MockLLM | None) -> LLMRouter:
    return LLMRouter(mock, {"fast": ["mock-fast"], "smart": ["mock-smart"]})


def make_deps(
    session_factory,
    fake: FakeNotion,
    mock: MockLLM | None,
    *,
    now: datetime,
    writes=True,
    user_id: uuid.UUID = OWNER_ID,
) -> WorkflowDeps:
    return WorkflowDeps(
        session_factory, fake, SPECS, router(mock), writes_enabled=writes, user_id=user_id,
        clock=lambda: now,
    )  # fmt: skip


def make_runner(
    session_factory, fake: FakeNotion, mock: MockLLM | None, *, now: datetime, writes=True
) -> tuple[WorkflowRunner, WorkflowDeps]:
    deps = make_deps(session_factory, fake, mock, now=now, writes=writes)
    return WorkflowRunner(deps, InMemorySaver()), deps


def make_services(
    session_factory,
    fake: FakeNotion,
    mock: MockLLM | None,
    *,
    now: datetime,
    settings: Settings | None = None,
    writes=True,
    **extra: Any,
) -> tuple[Services, WorkflowDeps]:
    """App services whose owner account works on `fake`; returns them with the owner's deps."""

    owner = make_deps(session_factory, fake, mock, now=now, writes=writes)

    def deps_for(user_id: uuid.UUID) -> WorkflowDeps:
        if user_id == OWNER_ID:
            return owner
        return make_deps(session_factory, fake, mock, now=now, writes=writes, user_id=user_id)

    contexts = Contexts(deps_for, InMemorySaver(), modes_for=partial(load_modes, session_factory))
    services = Services(
        settings or Settings(_env_file=None), session_factory, contexts, clock=lambda: now, **extra
    )
    return services, owner


async def add_user(session_factory, email: str, *, password: str | None = None, **fields) -> User:
    async with session_factory() as db, db.begin():
        user = User(
            email=email, password_hash=hash_password(password) if password else None, **fields
        )
        db.add(user)
        await db.flush()
        return user


async def set_password(session_factory, password: str, user_id: uuid.UUID = OWNER_ID) -> None:
    async with session_factory() as db, db.begin():
        await db.execute(
            update(User).where(User.id == user_id).values(password_hash=hash_password(password))
        )


def seed_task(fake: FakeNotion, title: str, **values: Any) -> str:
    values.setdefault("status", "Next")
    return fake.seed(SPECS["tasks"], {"title": title, **values})


def seed_period(fake: FakeNotion, source: str, title: str, start: date, end: date, **extra) -> str:
    values = {"title": title, "start": start, "end": end, "status": "Current", **extra}
    return fake.seed(SPECS[source], values)


def read(fake: FakeNotion, source: str, page_id: str) -> dict[str, Any]:
    """Logical field → decoded value, for assertions."""
    spec = SPECS[source]
    properties = fake.pages[page_id]["properties"]
    out: dict[str, Any] = {}
    for field, prop in spec.properties.items():
        raw = properties.get(prop.name)
        value = decode_property(raw) if raw else None
        if isinstance(value, DateValue):
            value = value.start
        if isinstance(value, RelationValue):
            value = list(value.ids)
        out[field] = value
    return out


def pages_in(fake: FakeNotion, source: str) -> dict[str, dict[str, Any]]:
    data_source = SPECS[source].data_source_id
    return {
        page_id: read(fake, source, page_id)
        for page_id, page in fake.pages.items()
        if page["parent"]["data_source_id"] == data_source
    }


def task(title: str, **values: Any) -> Task:
    """A domain Task for pure unit tests."""
    values.setdefault("status", "Next")
    return Task(
        id=values.pop("id", title),
        url="",
        title=title,
        last_edited_time=datetime(2026, 11, 1),
        **values,
    )
