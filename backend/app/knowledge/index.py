"""An account's knowledge index: Notion pages it chose, stored as passages (architecture §5.9).

Every passage gets a full-text vector (Postgres, always) and an embedding when the account's AI
can compute one. Search fuses the two rankings (reciprocal rank fusion), so exact words and
meaning both count; without embeddings it is keyword search alone. Pages are read again only
when the account refreshes them, and only rebuilt when Notion says they changed.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import KnowledgeChunk, KnowledgePage, User
from app.execution.audit import audit
from app.knowledge.extract import PageContent, read_page
from app.llm.base import LLMError

MAX_PAGES = 25
CANDIDATES = 20  # from each ranking, before fusion
RRF_K = 60
EMBED_BATCH = 100  # the API's limit per request


class KnowledgeFull(Exception):
    """The account already indexes `MAX_PAGES` pages."""


class EmptyPage(Exception):
    """The page has no text LifeOS can index."""


@dataclass(frozen=True)
class Hit:
    page: KnowledgePage
    heading: str
    text: str
    score: float


class KnowledgeIndex:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        notion: Any,
        llm: Any,  # LLMRouter or AccountLLM: anything with `embed`
        user_id: uuid.UUID,
        clock: Any = lambda: datetime.now(UTC),
    ) -> None:
        self.session_factory = session_factory
        self.notion = notion
        self.llm = llm
        self.user_id = user_id
        self.clock = clock
        self.warnings: list[str] = []

    async def pages(self) -> list[KnowledgePage]:
        async with self.session_factory() as db:
            rows = await db.scalars(
                select(KnowledgePage)
                .where(KnowledgePage.user_id == self.user_id)
                .order_by(func.lower(KnowledgePage.title))
            )
            return list(rows)

    async def get(self, row_id: uuid.UUID) -> KnowledgePage | None:
        async with self.session_factory() as db:
            return await db.scalar(self._mine(row_id))

    def _mine(self, row_id: uuid.UUID):
        return select(KnowledgePage).where(
            KnowledgePage.id == row_id, KnowledgePage.user_id == self.user_id
        )

    # --- changes ---------------------------------------------------------------------------

    async def add(self, page_id: str) -> KnowledgePage:
        """Read, split and store a page. An indexed page is refreshed instead. Raises
        KnowledgeFull, EmptyPage, or the Notion client's errors."""
        async with self.session_factory() as db:
            existing = await db.scalar(
                select(KnowledgePage).where(
                    KnowledgePage.user_id == self.user_id, KnowledgePage.page_id == page_id
                )
            )
        if existing is not None:
            row, _ = await self.refresh(existing.id, force=True)
            return row
        content = await read_page(self.notion, page_id)
        if not content.passages:
            raise EmptyPage
        vectors = await self._embed(content)
        row = KnowledgePage(id=uuid.uuid4(), user_id=self.user_id, page_id=content.page_id)
        async with self.session_factory() as db, db.begin():
            await db.get(User, self.user_id, with_for_update=True)  # one add at a time
            count = await db.scalar(
                select(func.count())
                .select_from(KnowledgePage)
                .where(KnowledgePage.user_id == self.user_id)
            )
            if (count or 0) >= MAX_PAGES:
                raise KnowledgeFull
            db.add(row)
            await self._fill(db, row, content, vectors)
            audit(db, actor="user", event="knowledge.add", user_id=self.user_id,
                  entity_type="knowledge_page", entity_id=str(row.id))  # fmt: skip
        return row

    async def refresh(
        self, row_id: uuid.UUID, *, force: bool = False
    ) -> tuple[KnowledgePage, bool]:
        """Read the page again; rebuild its passages if Notion says it changed (or it lacks
        embeddings that can now be computed). Returns the row and whether it was rebuilt.
        Raises LookupError for another account's or a removed page."""
        row = await self.get(row_id)
        if row is None:
            raise LookupError(row_id)
        page = await self.notion.get_page(row.page_id)
        edited = page.get("last_edited_time")
        same = (
            row.notion_edited_at is not None
            and edited is not None
            and row.notion_edited_at == datetime.fromisoformat(edited.replace("Z", "+00:00"))
        )
        if same and row.embedded and not force:
            return row, False
        content = await read_page(self.notion, row.page_id)
        vectors = await self._embed(content)
        async with self.session_factory() as db, db.begin():
            row = await db.scalar(self._mine(row_id).with_for_update())
            if row is None:
                raise LookupError(row_id)
            await db.execute(delete(KnowledgeChunk).where(KnowledgeChunk.page_ref == row.id))
            await self._fill(db, row, content, vectors)
            audit(db, actor="user", event="knowledge.refresh", user_id=self.user_id,
                  entity_type="knowledge_page", entity_id=str(row.id))  # fmt: skip
        return row, True

    async def remove(self, row_id: uuid.UUID) -> bool:
        async with self.session_factory() as db, db.begin():
            result = await db.execute(
                delete(KnowledgePage).where(
                    KnowledgePage.id == row_id, KnowledgePage.user_id == self.user_id
                )
            )
            if not result.rowcount:
                return False
            audit(db, actor="user", event="knowledge.remove", user_id=self.user_id,
                  entity_type="knowledge_page", entity_id=str(row_id))  # fmt: skip
        return True

    async def _fill(
        self,
        db: AsyncSession,
        row: KnowledgePage,
        content: PageContent,
        vectors: list[list[float]] | None,
    ) -> None:
        row.title = content.title[:300]
        row.url = content.url[:500]
        row.notion_edited_at = content.edited_at
        row.indexed_at = self.clock()
        row.passages = len(content.passages)
        row.embedded = vectors is not None
        row.truncated = content.truncated
        await db.flush()  # the page row first: passages reference it
        for position, passage in enumerate(content.passages):
            db.add(
                KnowledgeChunk(
                    user_id=self.user_id,
                    page_ref=row.id,
                    position=position,
                    heading=passage.heading[:600],
                    text=passage.text,
                    embedding=vectors[position] if vectors is not None else None,
                )  # fmt: skip
            )

    async def _embed(self, content: PageContent) -> list[list[float]] | None:
        """Embeddings for every passage, or None (and a warning) when AI can't make them; the
        page is then searchable by keyword until it is refreshed."""
        texts = [
            "\n".join(part for part in (content.title, p.heading, p.text) if part)
            for p in content.passages
        ]
        vectors: list[list[float]] = []
        try:
            for start in range(0, len(texts), EMBED_BATCH):
                result = await self.llm.embed(texts[start : start + EMBED_BATCH], task="document")
                vectors.extend(result.vectors)
        except LLMError as exc:
            self.warnings.append(_reason(exc))
            return None
        return vectors

    # --- search ----------------------------------------------------------------------------

    async def search(self, query: str, *, limit: int = 6) -> tuple[list[Hit], str]:
        """The best passages for `query`, and how they were found: "hybrid" (words and
        meaning) or "keyword"."""
        query = query.strip()
        if not query:
            return [], "keyword"
        ranked: list[list[uuid.UUID]] = []
        async with self.session_factory() as db:
            words = await db.execute(
                text(
                    # Any of the query's words, not all of them: questions are wordy.
                    "SELECT id FROM knowledge_chunks, CAST(replace(CAST(plainto_tsquery("
                    "'english', :q) AS text), ' & ', ' | ') AS tsquery) AS q "
                    "WHERE user_id = :user AND tsv @@ q "
                    "ORDER BY ts_rank_cd(tsv, q) DESC, position LIMIT :n"
                ),
                {"q": query, "user": self.user_id, "n": CANDIDATES},
            )
            ranked.append([r[0] for r in words])
            mode = "keyword"
            vector = await self._query_vector(query, db)
            if vector is not None:
                meaning = await db.execute(
                    text(
                        "SELECT id FROM knowledge_chunks "
                        "WHERE user_id = :user AND embedding IS NOT NULL "
                        "ORDER BY embedding <=> CAST(CAST(:v AS text) AS vector) LIMIT :n"
                    ),
                    {"v": vector, "user": self.user_id, "n": CANDIDATES},
                )
                ranked.append([r[0] for r in meaning])
                mode = "hybrid"
            scores: dict[uuid.UUID, float] = {}
            for ranking in ranked:
                for rank, chunk_id in enumerate(ranking):
                    scores[chunk_id] = scores.get(chunk_id, 0.0) + 1 / (RRF_K + rank + 1)
            best = sorted(scores, key=lambda c: -scores[c])[:limit]
            if not best:
                return [], mode
            rows = await db.execute(
                select(
                    KnowledgeChunk.id, KnowledgeChunk.heading, KnowledgeChunk.text, KnowledgePage
                )  # fmt: skip
                .join(KnowledgePage, KnowledgePage.id == KnowledgeChunk.page_ref)
                .where(KnowledgeChunk.id.in_(best))
            )
            found = {r[0]: Hit(r[3], r[1], r[2], scores[r[0]]) for r in rows}
        return [found[c] for c in best if c in found], mode

    async def _query_vector(self, query: str, db: AsyncSession) -> str | None:
        embedded = await db.scalar(
            select(func.count())
            .select_from(KnowledgeChunk)
            .where(KnowledgeChunk.user_id == self.user_id, KnowledgeChunk.embedding.is_not(None))
        )
        if not embedded:
            return None
        try:
            result = await self.llm.embed([query], task="query")
        except LLMError as exc:
            self.warnings.append(_reason(exc))
            return None
        return "[" + ",".join(f"{x:.7g}" for x in result.vectors[0]) + "]"


async def clear_index(db: AsyncSession, user_id: uuid.UUID) -> None:
    """Delete an account's indexed pages and passages: its copies of Notion content."""
    await db.execute(delete(KnowledgeChunk).where(KnowledgeChunk.user_id == user_id))
    await db.execute(delete(KnowledgePage).where(KnowledgePage.user_id == user_id))


def _reason(exc: LLMError) -> str:
    detail = str(exc) if type(exc).__name__ == "LLMOff" else "AI unavailable"
    return f"{detail}; searched by keyword only."
