from datetime import date

import pytest

from app.core.config import BACKEND_ROOT
from app.demo.seed import seed_workspace
from app.demo.setup import NotADemoDatabase, demo_notion, demo_specs, ensure_demo_database
from app.notion.workspace import build_workspace
from app.services.rules import Vocabulary, major_tasks, overdue

TODAY = date(2026, 11, 4)  # a Wednesday
SPECS = demo_specs(BACKEND_ROOT / "config" / "notion_mapping.yaml")


async def test_seed_builds_a_believable_week():
    notion = demo_notion()
    seed_workspace(notion, SPECS, TODAY)
    ws = build_workspace(notion, SPECS)
    vocab = Vocabulary.from_spec(SPECS["tasks"])
    active = await ws.tasks.active()
    assert len(major_tasks(active, TODAY, vocab)) == 3  # R1: today is full but not overfull
    assert [t.title for t in overdue(active, TODAY, vocab)] == [
        "Update resume summary", "Book dentist appointment",
    ]  # fmt: skip
    assert len(await ws.tasks.inbox()) == 2
    assert len(await ws.tasks.done_due_between(date(2026, 11, 2), TODAY)) == 2
    rows = await ws.habits.all()
    assert len(rows) == 6 and all(r.day and r.day < TODAY for r in rows)
    assert len(await ws.goals.active()) == 3
    assert len(await ws.books.queue()) == 3
    assert [p.title for p in await ws.weeks.covering(TODAY)] == ["2 Nov – 8 Nov"]
    status = await notion.get_data_source(SPECS["tasks"].data_source_id)
    assert "Status" in status["properties"]


async def test_reseeding_replaces_everything():
    notion = demo_notion()
    seed_workspace(notion, SPECS, TODAY)
    first = set(notion.pages)
    seed_workspace(notion, SPECS, TODAY)
    assert not first & set(notion.pages) and len(notion.pages) == len(first)


def test_demo_refuses_a_database_not_named_demo():
    ensure_demo_database("postgresql://u:p@host/lifeos_demo?sslmode=require")
    for url in ("postgresql://u:p@host/lifeos", "postgresql+asyncpg://u:p@localhost/lifeos_test"):
        with pytest.raises(NotADemoDatabase):
            ensure_demo_database(url)
