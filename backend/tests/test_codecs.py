from datetime import UTC, date, datetime

from app.notion.codecs import DateValue, RelationValue, decode_property
from tests.factories import rich


def test_text_select_and_checkbox():
    assert decode_property({"type": "title", "title": rich("A") + rich("B")}) == "AB"
    assert decode_property({"type": "rich_text", "rich_text": []}) == ""
    assert decode_property({"type": "select", "select": {"name": "Done"}}) == "Done"
    assert decode_property({"type": "select", "select": None}) is None
    assert decode_property({"type": "status", "status": {"name": "In progress"}}) == "In progress"
    assert decode_property(
        {"type": "multi_select", "multi_select": [{"name": "A"}, {"name": "B"}]}
    ) == ["A", "B"]
    assert decode_property({"type": "checkbox", "checkbox": True}) is True
    assert decode_property({"type": "number", "number": None}) is None


def test_dates_keep_date_only_values_as_dates():
    value = decode_property({"type": "date", "date": {"start": "2026-11-01", "end": None}})
    assert value == DateValue(start=date(2026, 11, 1))
    assert not value.is_datetime
    stamped = decode_property({"type": "date", "date": {"start": "2026-11-01T10:00:00.000Z"}})
    assert stamped.start == datetime(2026, 11, 1, 10, 0, tzinfo=UTC)
    assert decode_property({"type": "date", "date": None}) is None


def test_relation_reports_truncation():
    value = decode_property({"type": "relation", "relation": [{"id": "a"}], "has_more": True})
    assert value == RelationValue(ids=("a",), has_more=True)


def test_formula_and_rollup_unwrap_inner_type():
    assert (
        decode_property({"type": "formula", "formula": {"type": "string", "string": "2026-W44"}})
        == "2026-W44"
    )
    assert (
        decode_property({"type": "formula", "formula": {"type": "boolean", "boolean": False}})
        is False
    )
    assert (
        decode_property(
            {"type": "rollup", "rollup": {"type": "number", "number": 3, "function": "count"}}
        )
        == 3
    )
    nested = decode_property(
        {
            "type": "rollup",
            "rollup": {"type": "array", "array": [{"type": "title", "title": rich("x")}]},
        }
    )
    assert nested == ["x"]


def test_created_time_and_unknown_types():
    assert decode_property(
        {"type": "created_time", "created_time": "2026-10-27T10:55:24.000Z"}
    ) == datetime(2026, 10, 27, 10, 55, 24, tzinfo=UTC)
    assert decode_property({"type": "people", "people": [{"id": "u"}]}) == [{"id": "u"}]
