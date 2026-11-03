"""The knowledge index over HTTP: choose pages, refresh or remove them, and ask questions.

Asking is recorded as a run (workflow `knowledge`) so the Activity page counts its AI use; the
question and answer themselves aren't stored. The shared demo's pages are fixed.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import update

from app.agents.knowledge import answer_question
from app.api.errors import ApiError
from app.api.security import Context, services
from app.db.models import AgentRun, KnowledgePage
from app.knowledge.extract import page_title
from app.knowledge.index import MAX_PAGES, EmptyPage, KnowledgeFull
from app.notion.errors import NotionNotShared

router = APIRouter()


class AddBody(BaseModel):
    page_id: str = Field(min_length=32, max_length=64)


class AskBody(BaseModel):
    question: str = Field(min_length=1, max_length=500)


def _page(row: KnowledgePage) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "page_id": row.page_id,
        "title": row.title,
        "url": row.url,
        "passages": row.passages,
        "embedded": row.embedded,
        "truncated": row.truncated,
        "indexed_at": row.indexed_at.isoformat(),
        "notion_edited_at": row.notion_edited_at.isoformat() if row.notion_edited_at else None,
    }


def _writable(request: Request) -> None:
    if services(request).settings.lifeos_demo_mode:
        raise ApiError(403, "demo_account", "the shared demo's pages can't be changed")


@router.get("/knowledge")
async def knowledge(request: Request, ctx: Context) -> dict[str, Any]:
    rows = await ctx.deps.knowledge().pages()
    return {
        "pages": [_page(r) for r in rows],
        "limits": {"pages": MAX_PAGES},
        "editable": not services(request).settings.lifeos_demo_mode,
    }


@router.get("/knowledge/search-pages")
async def search_pages(ctx: Context, q: str = Query(..., min_length=1, max_length=100)) -> dict:
    """Notion pages the connection can see whose title matches, to pick from."""
    indexed = {r.page_id for r in await ctx.deps.knowledge().pages()}
    results = await ctx.deps.notion.search_pages(q, page_size=20)
    return {
        "results": [
            {"page_id": p["id"], "title": page_title(p) or "Untitled", "url": p.get("url") or "",
             "edited_at": p.get("last_edited_time"), "indexed": p["id"] in indexed}
            for p in results if not p.get("in_trash") and not p.get("archived")
        ]
    }  # fmt: skip


@router.post("/knowledge/pages", status_code=201)
async def add_page(body: AddBody, request: Request, ctx: Context) -> dict[str, Any]:
    _writable(request)
    index = ctx.deps.knowledge()
    try:
        row = await index.add(body.page_id)
    except KnowledgeFull as exc:
        raise ApiError(409, "knowledge_full", f"the index holds at most {MAX_PAGES} pages") from exc
    except EmptyPage as exc:
        raise ApiError(422, "empty_page", "that page has no text to index") from exc
    except NotionNotShared as exc:
        raise ApiError(404, "not_shared", "LifeOS can't see this page") from exc
    return {**_page(row), "warnings": index.warnings}


@router.post("/knowledge/pages/{row_id}/refresh")
async def refresh_page(row_id: uuid.UUID, request: Request, ctx: Context) -> dict[str, Any]:
    _writable(request)
    index = ctx.deps.knowledge()
    try:
        row, changed = await index.refresh(row_id)
    except LookupError as exc:
        raise ApiError(404, "unknown_page", "no such indexed page") from exc
    except NotionNotShared as exc:
        raise ApiError(404, "not_shared", "LifeOS can no longer see this page") from exc
    return {**_page(row), "changed": changed, "warnings": index.warnings}


@router.delete("/knowledge/pages/{row_id}", status_code=204)
async def remove_page(row_id: uuid.UUID, request: Request, ctx: Context) -> Response:
    _writable(request)
    if not await ctx.deps.knowledge().remove(row_id):
        raise ApiError(404, "unknown_page", "no such indexed page")
    return Response(status_code=204)


@router.post("/knowledge/ask")
async def ask(body: AskBody, ctx: Context) -> dict[str, Any]:
    deps = ctx.deps
    run_id = uuid.uuid4()
    async with deps.session_factory() as db, db.begin():
        db.add(AgentRun(id=run_id, user_id=deps.user_id, workflow="knowledge", trigger="ask",
                        thread_id=str(run_id), status="running"))  # fmt: skip
    index = deps.knowledge()
    status = "failed"
    usage: list[dict[str, Any]] = []
    try:
        hits, mode = await index.search(body.question)
        result = await answer_question(deps.llm, body.question, hits)
        usage = result.usage
        status = "completed"
    finally:
        values: dict[str, Any] = {
            "status": status,
            "finished_at": datetime.now(UTC),
            "output": {"llm_calls": len(usage), "sources": len(result.citations)}
            if status == "completed" else {"llm_calls": 0},
        }  # fmt: skip
        if usage:
            values.update(
                provider=usage[-1].get("provider"), model=usage[-1].get("model"),
                input_tokens=sum(u.get("input_tokens", 0) for u in usage),
                output_tokens=sum(u.get("output_tokens", 0) for u in usage),
                llm_latency_ms=sum(u.get("latency_ms", 0) for u in usage),
            )  # fmt: skip
        async with deps.session_factory() as db, db.begin():
            await db.execute(update(AgentRun).where(AgentRun.id == run_id).values(**values))
    return {
        "answer": result.answer,
        "found": result.found,
        "source": result.source,
        "search": mode,
        "citations": result.citations,
        "warnings": index.warnings + result.warnings,
    }
