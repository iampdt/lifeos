"""Usage and latency reports: counted from the account's own runs only."""

import uuid
from datetime import UTC, datetime, timedelta

import httpx

from app.api.main import create_app
from app.core.config import Settings
from app.db.models import AgentRun
from app.llm.mock import MockLLM
from tests.fake_notion import FakeNotion
from tests.workflow_support import OWNER_ID, add_user, make_services

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


def run(user_id, workflow, status, *, minutes=1, latency=0, tokens=(0, 0), calls=0, days_ago=0):
    started = NOW - timedelta(days=days_ago, hours=1)
    return AgentRun(
        id=uuid.uuid4(), user_id=user_id, workflow=workflow, trigger="test",
        thread_id="t", status=status, started_at=started,
        finished_at=started + timedelta(minutes=minutes) if status != "waiting_input" else None,
        model="gemini-3.5-flash-lite" if calls else None, input_tokens=tokens[0],
        output_tokens=tokens[1], llm_latency_ms=latency, output={"llm_calls": calls},
    )  # fmt: skip


async def test_usage_is_counted_per_workflow_and_day_for_the_account_only(session_factory):
    other = await add_user(session_factory, "other@example.com")
    async with session_factory() as db, db.begin():
        db.add_all([
            run(OWNER_ID, "evening_checkin", "completed", latency=800, tokens=(400, 60), calls=1),
            run(OWNER_ID, "evening_checkin", "completed", latency=1200, tokens=(500, 80), calls=1,
                days_ago=1),
            run(OWNER_ID, "evening_checkin", "failed"),
            run(OWNER_ID, "weekly_review", "waiting_input", latency=3000, tokens=(2000, 400),
                calls=1),
            run(OWNER_ID, "chat", "completed", days_ago=40),  # outside the window
            run(other.id, "evening_checkin", "completed", tokens=(9999, 9999), calls=5),
        ])  # fmt: skip
    settings = Settings(_env_file=None, lifeos_api_token="tok")
    services, _ = make_services(
        session_factory, FakeNotion(), MockLLM(), now=NOW, settings=settings
    )
    transport = httpx.ASGITransport(app=create_app(services))
    async with httpx.AsyncClient(transport=transport, base_url="https://t") as client:
        report = (
            await client.get("/api/v1/reports/usage", headers={"Authorization": "Bearer tok"})
        ).json()
    totals = report["totals"]
    assert totals["runs"] == 4 and totals["llm_calls"] == 3
    assert totals["input_tokens"] == 2900 and totals["output_tokens"] == 540
    evening = report["workflows"]["evening_checkin"]
    assert evening["runs"] == 3 and evening["success_rate"] == 2 / 3
    assert evening["llm_latency_ms"] == {"p50": 1000, "p95": 1200}
    assert evening["duration_ms"]["p50"] == 60_000
    assert report["workflows"]["weekly_review"]["statuses"]["waiting_input"] == 1
    assert sum(d["runs"] for d in report["by_day"].values()) == 4
    assert report["models"] == {"gemini-3.5-flash-lite": 3}
