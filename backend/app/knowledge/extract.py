"""A Notion page's content as passages for the knowledge index (architecture §5.9).

The page's blocks are read in order, nested ones down to `MAX_DEPTH`, and the text of text-like
blocks (paragraphs, lists, to-dos, toggles, quotes, callouts, code, tables) is grouped under its
headings into passages of about `PASSAGE_CHARS`. Each passage keeps its heading path, so it
still makes sense on its own in a search result. Child pages and databases are left out: they
are separate pages, indexed separately if wanted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

PASSAGE_CHARS = 1200
MAX_PASSAGES = 60
MAX_BLOCKS = 1500
MAX_DEPTH = 3
MAX_CHILD_READS = 40  # nested blocks need a request each

HEADINGS = {"heading_1": 1, "heading_2": 2, "heading_3": 3}
TEXT_BLOCKS = {
    "paragraph", "bulleted_list_item", "numbered_list_item", "to_do", "toggle", "quote",
    "callout", "code", "table_row",
}  # fmt: skip
CONTAINERS = {"column_list", "column", "synced_block", "table"}  # no text of their own
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class Passage:
    heading: str  # "Heading › Subheading", or "" before the first heading
    text: str


@dataclass
class PageContent:
    page_id: str
    title: str
    url: str
    edited_at: datetime | None
    passages: list[Passage] = field(default_factory=list)
    truncated: bool = False  # the page was longer than the index keeps


def rich_text(items: list[dict[str, Any]] | None) -> str:
    return "".join(item.get("plain_text", "") for item in items or [])


def page_title(page: dict[str, Any]) -> str:
    for prop in (page.get("properties") or {}).values():
        if prop.get("type") == "title":
            return rich_text(prop.get("title")).strip()
    return ""


def _edited(page: dict[str, Any]) -> datetime | None:
    raw = page.get("last_edited_time")
    return datetime.fromisoformat(raw.replace("Z", "+00:00")) if raw else None


def block_line(block: dict[str, Any]) -> str:
    kind = block.get("type", "")
    body = block.get(kind) or {}
    if kind == "table_row":
        return " | ".join(rich_text(cell).strip() for cell in body.get("cells") or [])
    text = rich_text(body.get("rich_text")).strip()
    if not text:
        return ""
    if kind in ("bulleted_list_item", "numbered_list_item"):
        return f"- {text}"
    if kind == "to_do":
        return f"[{'x' if body.get('checked') else ' '}] {text}"
    return text


class _Builder:
    def __init__(self) -> None:
        self.headings: dict[int, str] = {}
        self.lines: list[str] = []
        self.size = 0
        self.passages: list[Passage] = []

    def heading(self, level: int, text: str) -> None:
        self.flush()
        self.headings = {k: v for k, v in self.headings.items() if k < level}
        if text:
            self.headings[level] = text

    def line(self, text: str) -> None:
        for piece in _split(text):
            if self.size + len(piece) > PASSAGE_CHARS and self.lines:
                self.flush()
            self.lines.append(piece)
            self.size += len(piece) + 1

    def flush(self) -> None:
        if self.lines:
            path = " › ".join(self.headings[k] for k in sorted(self.headings))
            self.passages.append(Passage(path, "\n".join(self.lines)))
        self.lines, self.size = [], 0


def _split(text: str) -> list[str]:
    """A block longer than a passage, cut at sentence ends (or hard, as a last resort)."""
    if len(text) <= PASSAGE_CHARS:
        return [text]
    pieces, current = [], ""
    for sentence in _SENTENCE.split(text):
        if current and len(current) + len(sentence) + 1 > PASSAGE_CHARS:
            pieces.append(current)
            current = ""
        while len(sentence) > PASSAGE_CHARS:
            pieces.append(sentence[:PASSAGE_CHARS])
            sentence = sentence[PASSAGE_CHARS:]
        current = f"{current} {sentence}".strip()
    if current:
        pieces.append(current)
    return pieces


async def read_page(notion: Any, page_id: str) -> PageContent:
    """The page's title, link, last edit and passages. Raises the client's errors (a page the
    integration can't see is `NotionNotShared`)."""
    page = await notion.get_page(page_id)
    content = PageContent(page["id"], page_title(page) or "Untitled", page.get("url") or "",
                          _edited(page))  # fmt: skip
    builder = _Builder()
    budget = {"blocks": MAX_BLOCKS, "reads": MAX_CHILD_READS}

    async def walk(block_id: str, depth: int) -> None:
        async for block in notion.list_block_children(block_id):
            if budget["blocks"] <= 0:
                content.truncated = True
                return
            budget["blocks"] -= 1
            kind = block.get("type", "")
            if kind in HEADINGS:
                builder.heading(HEADINGS[kind], rich_text(block[kind].get("rich_text")).strip())
            elif kind in TEXT_BLOCKS and (line := block_line(block)):
                builder.line(line)
            nested = kind in TEXT_BLOCKS | CONTAINERS or kind in HEADINGS  # toggle headings
            if block.get("has_children") and nested:
                if depth >= MAX_DEPTH or budget["reads"] <= 0:
                    content.truncated = True
                    continue
                budget["reads"] -= 1
                await walk(block["id"], depth + 1)

    await walk(page_id, 1)
    builder.flush()
    content.passages = builder.passages[:MAX_PASSAGES]
    content.truncated = content.truncated or len(builder.passages) > MAX_PASSAGES
    return content
