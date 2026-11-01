"""Real Notion writes, only to the "LifeOS Sandbox (dev)" databases.

Skipped unless LIFEOS_LIVE_TESTS=1. Needs the test Postgres (docker compose up -d db).
Each run adds one task to the sandbox; the 2099 week, month and habit row are reused.
"""

import uuid
from datetime import date

import pytest

from app.core.config import env_values, get_settings
from app.execution.actions import (
    ActionKind,
    CreatePeriod,
    CreateTask,
    MarkDone,
    SetDue,
    UpsertHabitDay,
)
from app.execution.executor import Executor, take_snapshot
from app.execution.proposals import ProposalService
from app.notion.client import NotionClient
from app.notion.mapping import load_mapping
from app.notion.repositories import TaskRepository
from app.services.calendar import month_range, week_key, week_range

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        env_values().get("LIFEOS_LIVE_TESTS") != "1", reason="set LIFEOS_LIVE_TESTS=1"
    ),
]

FIRST_DUE = date(2099, 1, 2)
NEW_DUE = date(2099, 1, 6)


async def test_full_write_cycle_on_the_sandbox(session_factory):
    settings = get_settings()
    sandbox = load_mapping(settings.notion_mapping_path, env_values(), "sandbox")
    live = load_mapping(settings.notion_mapping_path, env_values(), "live")
    for name, spec in sandbox.items():  # never let this test touch the real Life OS
        if spec.configured:
            assert spec.data_source_id != live[name].data_source_id
    specs = {name: spec for name, spec in sandbox.items() if spec.configured}

    async with NotionClient(settings.notion_token.get_secret_value()) as client:
        executor = Executor(session_factory, client, specs, writes_enabled=True, actor="test")

        async def run(kind, payload, **kwargs):
            async with session_factory() as session, session.begin():
                service = ProposalService(session)
                proposal, _ = await service.propose(kind, payload, reason="sandbox test", **kwargs)
                await service.approve(proposal.id)
            [result] = await executor.execute_approved([proposal.id])
            assert result.outcome in ("verified", "noop"), result
            return result

        week_start, week_end = week_range(NEW_DUE)
        month_start, month_end = month_range(NEW_DUE)
        week = await run(
            ActionKind.WEEK_CREATE,
            CreatePeriod(title="Sandbox week (2099)", start=week_start, end=week_end),
        )
        month = await run(
            ActionKind.MONTH_CREATE,
            CreatePeriod(title="Sandbox month (2099)", start=month_start, end=month_end),
        )

        title = f"LifeOS sandbox test {uuid.uuid4().hex[:8]}"
        created = await run(
            ActionKind.TASK_CREATE,
            CreateTask(title=title, status="next", due=FIRST_DUE, priority="p1"),
        )
        assert created.outcome == "verified"

        move = SetDue(
            page_id=created.page_id,
            new_due=NEW_DUE,
            decision="carry_forward",
            week_page_id=week.page_id,
            month_page_id=month.page_id,
        )
        snapshot, edited_at = await take_snapshot(client, specs, ActionKind.TASK_SET_DUE, move)
        moved = await run(
            ActionKind.TASK_SET_DUE, move, before_snapshot=snapshot, target_last_edited_at=edited_at
        )
        assert moved.outcome == "verified"

        done = await run(
            ActionKind.TASK_MARK_DONE, MarkDone(page_id=created.page_id, completed_on=NEW_DUE)
        )
        assert done.outcome == "verified"

        await run(
            ActionKind.HABIT_UPSERT_DAY,
            UpsertHabitDay(day=FIRST_DUE, checks={"reading": True}, sleep=7.5),
        )

        # Read the task back through the normal read path.
        task = await TaskRepository(client, specs["tasks"]).get(created.page_id)
        assert (task.title, task.status, task.due, task.completed) == (
            title,
            "Done",
            NEW_DUE,
            NEW_DUE,
        )
        canonical = lambda i: i.replace("-", "")  # noqa: E731
        assert [canonical(i) for i in task.week_ids] == [canonical(week.page_id)]
        assert [canonical(i) for i in task.month_ids] == [canonical(month.page_id)]
        assert task.week_key == week_key(NEW_DUE)
