"""Onboarding suggestions: databases per module, properties per field, status meanings."""

from app.demo.notion import schema_for
from app.notion.mapping import DataSourceSpec
from app.workspaces.modules import MODULES, validate_mapping
from app.workspaces.store import specs_from_mapping
from app.workspaces.suggest import (
    classify_options,
    describe_schema,
    detect_date_format,
    field_key,
    rank_priorities,
    suggest_module,
    suggest_sources,
)
from tests.workflow_support import SPECS


def props(*items) -> list[dict]:
    """(name, type) or (name, type, options) → described properties."""
    return [{"name": i[0], "type": i[1], "options": list(i[2]) if len(i) > 2 else []}
            for i in items]  # fmt: skip


def test_databases_are_matched_to_modules_by_title():
    sources = [
        {"id": "1", "title": "Random notes"}, {"id": "2", "title": "Habit tracker"},
        {"id": "3", "title": "My Tasks"}, {"id": "4", "title": "Weekly Progress"},
        {"id": "5", "title": "Monthly Progress"}, {"id": "6", "title": "Reading list"},
    ]  # fmt: skip
    assert suggest_sources(sources) == {
        "tasks": "3", "habits": "2", "weeks": "4", "months": "5", "goals": None, "books": "6",
    }  # fmt: skip


def test_notions_default_task_list_is_mapped_from_names_and_options():
    schema = props(
        ("Name", "title"),
        ("Status", "status", ("Not started", "In progress", "Done")),
        ("Due date", "date"),
        ("Priority", "select", ("Low", "Medium", "High")),
        ("Tags", "multi_select", ("Work",)),
    )
    mapped = suggest_module("tasks", schema)["properties"]
    assert mapped["title"]["name"] == "Name" and mapped["due"]["name"] == "Due date"
    assert mapped["status"]["values"] == {"next": "Not started", "doing": "In progress",
                                          "done": "Done"}  # fmt: skip
    assert mapped["priority"]["values"] == {"p0": "High", "p1": "Medium", "p2": "Low"}
    assert "area" not in mapped  # a multi-select isn't an area


def test_status_meanings_use_the_longest_matching_phrase():
    books = MODULES["books"].fields["status"]
    values = classify_options("books", ["Want to read", "Reading", "Read", "Gave up"], books)
    assert values == {"reading": "Reading", "want": "Want to read", "finished": "Read",
                      "abandoned": "Gave up"}  # fmt: skip
    tasks = MODULES["tasks"].fields["status"]
    assert classify_options("tasks", ["Not started", "Started"], tasks) == {
        "next": "Not started", "doing": "Started",
    }  # fmt: skip


def test_priorities_rank_from_highest_whatever_their_names():
    assert rank_priorities(["P2", "P0", "P1"]) == {"p0": "P0", "p1": "P1", "p2": "P2"}
    assert rank_priorities(["Someday", "Urgent", "Normal"]) == {
        "p0": "Urgent", "p1": "Normal", "p2": "Someday",
    }  # fmt: skip


def test_habits_take_every_checkbox_and_number_with_a_unit_guess():
    schema = props(("Name", "title"), ("Date", "date"), ("Meditate", "checkbox"),
                   ("Exercise", "checkbox"), ("Sleep (hours)", "number"), ("Steps", "number"),
                   ("Notes", "rich_text"))  # fmt: skip
    mapped = suggest_module("habits", schema)["properties"]
    assert mapped["day"]["name"] == "Date" and mapped["notes"]["name"] == "Notes"
    assert mapped["meditate"]["type"] == "checkbox" and mapped["exercise"]["type"] == "checkbox"
    assert mapped["sleep_hours"]["unit"] == "hours" and mapped["steps"]["unit"] == "steps"


def test_field_keys_are_identifiers_and_unique():
    taken = {"title"}
    assert field_key("Title", taken) == "title_2"
    assert field_key("10k steps", taken) == "f_10k_steps"
    assert field_key("💧 Water", taken) == "water"


def test_title_date_formats_are_detected_from_samples():
    assert detect_date_format(["01/11/2026", "15/10/2026"]) == "%d/%m/%Y"
    assert detect_date_format(["11/15/2026"]) == "%m/%d/%Y"
    assert detect_date_format(["2026-11-01"]) == "%Y-%m-%d"
    assert detect_date_format(["November 1, 2026"]) == "%B %d, %Y"
    assert detect_date_format(["Monday"]) is None and detect_date_format([]) is None


def test_a_workspace_laid_out_like_the_template_is_mapped_completely():
    """The owner's layout is what the starter template uses: every field and meaning the
    mapping depends on is found from names alone."""
    for module in ("tasks", "habits", "weeks", "months", "goals", "books"):
        expected: DataSourceSpec = SPECS[module]
        live = describe_schema(schema_for(expected))
        mapped = suggest_module(module, live)["properties"]
        names = {field: spec["name"] for field, spec in mapped.items()}
        wanted = {f: p.name for f, p in expected.properties.items()
                  if f in MODULES[module].fields}  # fmt: skip
        assert {f: names.get(f) for f in wanted} == wanted, module
        for field, prop in expected.properties.items():
            rule = MODULES[module].fields.get(field)
            if prop.values and rule and (rule.needs or rule.allows or field == "priority"):
                assert mapped[field]["values"] == prop.values, (module, field)
    suggested = {m: {"data_source_id": f"ds-{m}", **suggest_module(m, describe_schema(
        schema_for(SPECS[m])))} for m in ("tasks", "habits")}  # fmt: skip
    assert validate_mapping(specs_from_mapping(suggested)) == []


TEMPLATE = {  # docs/template.md
    "tasks": props(
        ("Task", "title"), ("Status", "select", ("Inbox", "Next", "Doing", "Waiting", "Done",
                                                 "Cancelled")),
        ("Due", "date"), ("Priority", "select", ("P0", "P1", "P2", "P3")), ("Area", "select"),
        ("Completed", "date"), ("Recurring", "checkbox"), ("Goal", "relation"),
        ("Week", "relation"), ("Month", "relation"),
    ),
    "habits": props(("Name", "title"), ("Date", "date"), ("Exercise", "checkbox"),
                    ("Read", "checkbox"), ("Deep work", "checkbox"),
                    ("Sleep (hours)", "number"), ("Notes", "rich_text")),
    "weeks": props(("Week", "title"), ("Start", "date"), ("End", "date"),
                   ("Status", "select", ("Upcoming", "Current", "Closed")),
                   ("Theme", "rich_text"), ("Review", "rich_text"), ("Tasks", "relation")),
    "goals": props(("Goal", "title"),
                   ("Status", "select", ("Planned", "Active", "Done", "Dropped")),
                   ("Type", "select", ("Year", "Quarter")), ("Current", "number"),
                   ("Target", "number"), ("Deadline", "date")),
    "books": props(("Book", "title"), ("Author", "rich_text"),
                   ("Status", "select", ("Want to Read", "Reading", "Finished", "Abandoned")),
                   ("Pages", "number"), ("Pages Read", "number"), ("Started", "date"),
                   ("Finished", "date")),
}  # fmt: skip


def test_the_starter_template_is_mapped_completely():
    mapping = {}
    for module, schema in TEMPLATE.items():
        mapped = suggest_module(module, schema)
        assert {p["name"] for p in mapped["properties"].values()} == {p["name"] for p in schema}
        mapping[module] = {"data_source_id": module, **mapped}
    assert validate_mapping(specs_from_mapping(mapping)) == []
    tasks = mapping["tasks"]["properties"]["status"]["values"]
    assert set(tasks) == {"inbox", "next", "doing", "waiting", "done", "cancelled"}
