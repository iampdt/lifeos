"""Shared setup for workflow and API tests: FakeNotion + MockLLM + in-memory checkpointer."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from app.domain.models import Task
from app.llm.mock import MockLLM
from app.llm.router import LLMRouter
from app.notion.codecs import DateValue, RelationValue, decode_property
from app.workflows.deps import WorkflowDeps
from app.workflows.runner import WorkflowRunner
from tests.factories import specs
from tests.fake_notion import FakeNotion

SPECS = specs()


def router(mock: MockLLM | None) -> LLMRouter:
    return LLMRouter(mock, {"fast": ["mock-fast"], "smart": ["mock-smart"]})


def make_runner(
    session_factory, fake: FakeNotion, mock: MockLLM | None, *, now: datetime, writes=True
) -> tuple[WorkflowRunner, WorkflowDeps]:
    deps = WorkflowDeps(
        session_factory, fake, SPECS, router(mock), writes_enabled=writes, clock=lambda: now
    )
    return WorkflowRunner(deps, InMemorySaver()), deps


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
