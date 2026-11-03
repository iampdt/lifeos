"""Notion blocks → passages for the knowledge index."""

from app.knowledge import extract
from app.knowledge.extract import PASSAGE_CHARS, read_page


def text(content: str) -> list[dict]:
    return [{"plain_text": content}]


def block(kind: str, content: str = "", *, children: list | None = None, **extra) -> dict:
    body = {"rich_text": text(content), **extra}
    return {"id": f"{kind}-{content[:12]}", "type": kind, "has_children": bool(children),
            kind: body, "_children": children or []}  # fmt: skip


class Pages:
    """Just enough of the Notion client: one page and its block tree."""

    def __init__(self, blocks: list[dict]) -> None:
        self.blocks = blocks
        self.reads = 0

    async def get_page(self, page_id: str) -> dict:
        return {"id": page_id, "url": "https://notion.so/p",
                "last_edited_time": "2026-11-03T10:00:00.000Z",
                "properties": {"Name": {"type": "title", "title": text("Plan")}}}  # fmt: skip

    async def list_block_children(self, block_id: str):
        self.reads += 1
        children = self.blocks if block_id == "page" else self._find(self.blocks, block_id)
        for item in children:
            yield item

    def _find(self, blocks: list[dict], block_id: str) -> list[dict]:
        for item in blocks:
            if item["id"] == block_id:
                return item["_children"]
            found = self._find(item["_children"], block_id)
            if found:
                return found
        return []


async def test_text_is_grouped_under_its_headings():
    notion = Pages([
        block("paragraph", "Intro line."),
        block("heading_1", "Prep"),
        block("heading_2", "Design"),
        block("bulleted_list_item", "Cover failure modes"),
        block("to_do", "Book a mock", checked=True),
        block("heading_2", "Coding"),
        block("numbered_list_item", "Three problems a day"),
        block("toggle", "Details", children=[block("paragraph", "Hidden in a toggle")]),
        block("child_page", "Ignored"),
        block("heading_1", "Empty section"),
    ])  # fmt: skip
    content = await read_page(notion, "page")
    assert content.title == "Plan" and content.url == "https://notion.so/p"
    assert content.edited_at.isoformat() == "2026-11-03T10:00:00+00:00"
    assert [(p.heading, p.text) for p in content.passages] == [
        ("", "Intro line."),
        ("Prep › Design", "- Cover failure modes\n[x] Book a mock"),
        ("Prep › Coding", "- Three problems a day\nDetails\nHidden in a toggle"),
    ]
    assert not content.truncated


async def test_long_text_is_split_into_passages():
    sentence = "This sentence is about forty characters. "
    notion = Pages([block("paragraph", sentence * 70), block("paragraph", "Tail.")])
    content = await read_page(notion, "page")
    assert len(content.passages) > 2
    assert all(len(p.text) <= PASSAGE_CHARS + 1 for p in content.passages)
    assert content.passages[-1].text.endswith("Tail.")
    # Nothing is lost when splitting.
    joined = " ".join(p.text.replace("\n", " ") for p in content.passages)
    assert joined.count("forty characters") == 70


async def test_deep_nesting_and_huge_pages_are_cut_and_flagged(monkeypatch):
    deep = block("paragraph", "level 4")
    for level in (3, 2, 1):
        deep = block("toggle", f"level {level}", children=[deep])
    content = await read_page(Pages([deep]), "page")
    assert "level 3" in content.passages[0].text and "level 4" not in content.passages[0].text
    assert content.truncated

    monkeypatch.setattr(extract, "MAX_PASSAGES", 2)
    many = [block("heading_1", f"H{i}") if i % 2 else block("paragraph", f"Text {i}")
            for i in range(10)]  # fmt: skip
    content = await read_page(Pages(many), "page")
    assert len(content.passages) == 2 and content.truncated


def test_table_rows_keep_their_cells():
    row = {"type": "table_row", "table_row": {"cells": [text("Key"), text("Value")]}}
    assert extract.block_line(row) == "Key | Value"
