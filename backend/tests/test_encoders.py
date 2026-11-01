from datetime import date

import pytest

from app.notion.encoders import MAX_TEXT_CHUNK, NotWritable, encode_properties, encode_value
from app.notion.mapping import PropertySpec


def spec(kind: str) -> PropertySpec:
    return PropertySpec(name="P", type=kind)


def test_encodes_each_writable_type():
    assert encode_value(spec("title"), "Hi") == {
        "title": [{"type": "text", "text": {"content": "Hi"}}]
    }
    assert encode_value(spec("select"), "Done") == {"select": {"name": "Done"}}
    assert encode_value(spec("select"), None) == {"select": None}
    assert encode_value(spec("status"), "Doing") == {"status": {"name": "Doing"}}
    assert encode_value(spec("date"), date(2026, 11, 3)) == {"date": {"start": "2026-11-03"}}
    assert encode_value(spec("date"), None) == {"date": None}
    assert encode_value(spec("checkbox"), 1) == {"checkbox": True}
    assert encode_value(spec("number"), 7.5) == {"number": 7.5}
    assert encode_value(spec("relation"), ["a", "b"]) == {"relation": [{"id": "a"}, {"id": "b"}]}
    assert encode_value(spec("multi_select"), ["x"]) == {"multi_select": [{"name": "x"}]}


def test_long_text_is_split_into_notion_sized_chunks():
    body = encode_value(spec("rich_text"), "x" * (MAX_TEXT_CHUNK * 2 + 5))["rich_text"]
    assert [len(part["text"]["content"]) for part in body] == [MAX_TEXT_CHUNK, MAX_TEXT_CHUNK, 5]


def test_computed_properties_are_not_writable():
    for kind in ("formula", "rollup", "created_time"):
        with pytest.raises(NotWritable):
            encode_value(spec(kind), "x")


def test_dates_must_be_dates():
    with pytest.raises(TypeError):
        encode_value(spec("date"), "2026-11-03")


def test_properties_are_keyed_by_notion_display_name():
    specs = {"due": PropertySpec(name="Due", type="date")}
    assert encode_properties(specs, {"due": date(2026, 1, 2)}) == {
        "Due": {"date": {"start": "2026-01-02"}}
    }
