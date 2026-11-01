"""Async engine, session factory, and database URL handling.

`DATABASE_URL` may be a standard libpq URL, as Neon provides it
(`postgresql://…?sslmode=require&channel_binding=require`), or a SQLAlchemy asyncpg URL. The
app needs two forms of it: asyncpg for SQLAlchemy, and libpq/psycopg for LangGraph's checkpointer.
"""

from __future__ import annotations

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

_PLAIN_POSTGRES = {"postgres", "postgresql", "postgresql+psycopg", "postgresql+psycopg2"}


def async_url(raw: str) -> str:
    """asyncpg form: driver `postgresql+asyncpg`, `sslmode` → `ssl`, libpq-only options dropped."""
    url = make_url(raw)
    if url.drivername in _PLAIN_POSTGRES:
        url = url.set(drivername="postgresql+asyncpg")
    query = dict(url.query)
    sslmode = query.pop("sslmode", None)
    query.pop("channel_binding", None)  # asyncpg negotiates SCRAM channel binding itself
    if sslmode and "ssl" not in query:
        query["ssl"] = sslmode
    return url.set(query=query).render_as_string(hide_password=False)


def libpq_url(raw: str) -> str:
    """The libpq/psycopg form used by LangGraph's Postgres checkpointer."""
    url = make_url(raw).set(drivername="postgresql")
    query = dict(url.query)
    ssl = query.pop("ssl", None)
    if ssl and "sslmode" not in query:
        query["sslmode"] = ssl
    return url.set(query=query).render_as_string(hide_password=False)


def make_engine(database_url: str) -> AsyncEngine:
    # Small pool: one owner, and the free Postgres tier limits connections. pool_pre_ping
    # replaces connections dropped while a scale-to-zero database was suspended.
    return create_async_engine(
        async_url(database_url), pool_size=5, max_overflow=2, pool_pre_ping=True
    )


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
