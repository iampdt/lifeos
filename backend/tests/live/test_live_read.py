"""Read-only checks against the real Notion workspace. Skipped unless LIFEOS_LIVE_TESTS=1."""

import pytest

from app.core.config import env_values, get_settings
from app.notion.client import NotionClient
from app.notion.mapping import load_mapping
from app.notion.schema import check_data_source
from app.notion.workspace import build_workspace

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        env_values().get("LIFEOS_LIVE_TESTS") != "1", reason="set LIFEOS_LIVE_TESTS=1"
    ),
]


@pytest.fixture
async def client():
    settings = get_settings()
    async with NotionClient(settings.notion_token.get_secret_value()) as notion:
        yield notion


async def test_token_is_valid(client):
    assert (await client.get_self())["object"] == "user"


@pytest.mark.parametrize("profile", ["live", "sandbox"])
async def test_configured_schemas_have_no_drift(client, profile):
    specs = load_mapping(get_settings().notion_mapping_path, env_values(), profile)
    for spec in specs.values():
        if spec.configured:
            report = await check_data_source(client, spec)
            assert report.ok, (spec.logical_name, report.issues)


async def test_active_tasks_can_be_read(client):
    specs = load_mapping(get_settings().notion_mapping_path, env_values(), "live")
    workspace = build_workspace(client, specs)
    tasks = await workspace.tasks.active()
    assert all(t.status not in ("Done", "Cancelled") for t in tasks)
