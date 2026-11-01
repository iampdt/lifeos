import copy

import pytest

from app.notion.mapping import PropertySpec, load_mapping
from app.notion.schema import compare_schema, schema_hash
from tests.factories import FAKE_ENV, MAPPING_PATH, data_source_schema, specs


def test_mapping_resolves_ids_from_environment():
    loaded = specs()
    assert loaded["tasks"].data_source_id == "ds-tasks"
    assert loaded["tasks"].database_id == "db-tasks"
    assert loaded["tasks"].prop("status").value("done") == "Done"


def test_sandbox_profile_reads_sandbox_variables():
    env = {"NOTION_SANDBOX_DS_TASKS": "sandbox-tasks", **FAKE_ENV}
    sandbox = load_mapping(MAPPING_PATH, env, "sandbox")
    assert sandbox["tasks"].data_source_id == "sandbox-tasks"
    assert sandbox["tasks"].env == "NOTION_SANDBOX_DS_TASKS"
    assert not sandbox["goals"].configured  # no sandbox copy of Goals


def test_unknown_property_type_is_rejected():
    with pytest.raises(ValueError):
        PropertySpec(name="X", type="hologram")


def test_matching_schema_has_no_drift():
    spec = specs()["tasks"]
    assert compare_schema(spec, data_source_schema(spec)["properties"]) == []


def test_renamed_option_missing_property_and_type_change_are_reported():
    spec = specs()["tasks"]
    properties = copy.deepcopy(data_source_schema(spec)["properties"])
    properties["Status"]["select"]["options"] = [
        o for o in properties["Status"]["select"]["options"] if o["name"] != "Done"
    ]
    del properties["Due"]
    properties["Recurring"]["type"] = "rich_text"
    issues = {(i.field, i.problem) for i in compare_schema(spec, properties)}
    assert issues == {("status", "missing_option"), ("due", "missing"), ("recurring", "wrong_type")}
    assert all(i.blocking for i in compare_schema(spec, properties))


def test_optional_property_missing_is_not_blocking():
    spec = specs()["tasks"]
    spec.properties["energy"] = PropertySpec(name="Energy", type="select", required=False)
    properties = data_source_schema(spec)["properties"]
    del properties["Energy"]
    [issue] = compare_schema(spec, properties)
    assert issue.problem == "optional_missing" and not issue.blocking


def test_schema_hash_is_stable_and_order_independent():
    spec = specs()["tasks"]
    properties = data_source_schema(spec)["properties"]
    reordered = dict(reversed(list(properties.items())))
    assert schema_hash(properties) == schema_hash(reordered)
    properties["New"] = {"name": "New", "type": "checkbox", "checkbox": {}}
    assert schema_hash(properties) != schema_hash(reordered)
