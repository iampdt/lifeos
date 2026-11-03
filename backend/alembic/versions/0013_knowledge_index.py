"""Knowledge index: indexed Notion pages and their passages, with full-text and vector search

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "knowledge_pages",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("page_id", sa.String(64), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("url", sa.String(500), nullable=False),
        sa.Column("notion_edited_at", sa.DateTime(timezone=True)),
        sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("passages", sa.Integer, nullable=False),
        sa.Column("embedded", sa.Boolean, nullable=False),
        sa.Column("truncated", sa.Boolean, nullable=False),
        sa.UniqueConstraint("user_id", "page_id"),
    )
    op.create_table(
        "knowledge_chunks",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "page_ref",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("knowledge_pages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column("heading", sa.String(600), nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column(
            "tsv",
            pg.TSVECTOR,
            sa.Computed("to_tsvector('english', heading || ' ' || text)", persisted=True),
        ),
    )
    # Each account has at most a few thousand passages, searched by exact distance after
    # filtering on the account; an approximate index would only add memory on the free tier.
    op.execute("ALTER TABLE knowledge_chunks ADD COLUMN embedding vector(768)")
    op.create_index("ix_knowledge_chunks_user_id", "knowledge_chunks", ["user_id"])
    op.create_index("ix_knowledge_chunks_tsv", "knowledge_chunks", ["tsv"], postgresql_using="gin")


def downgrade() -> None:
    op.drop_table("knowledge_chunks")
    op.drop_table("knowledge_pages")
    # The extension stays: it holds no data, and something else may use it.
