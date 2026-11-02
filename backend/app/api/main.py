"""FastAPI application: `uvicorn app.api.main:app` (docs/api.md).

Services (Notion client, database, LLM router, LangGraph checkpointer) are built once in the
lifespan; each account's workflow dependencies and runner come from `contexts`. Tests pass
ready-made `Services` to `create_app` instead.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from functools import partial
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.cron import router as cron_router
from app.api.errors import ApiError, error_body
from app.api.routes import router
from app.auth.google import GoogleOIDC
from app.auth.sessions import RateLimiter
from app.core.config import Settings, env_values, get_settings
from app.db.models import User
from app.db.session import make_engine, make_session_factory
from app.demo.setup import demo_notion, demo_specs, demo_user, ensure_demo_database, reset_demo
from app.llm.factory import build_router
from app.notion.client import NotionClient
from app.notion.errors import NotionError
from app.notion.mapping import load_mapping
from app.notion.rate_limiter import AsyncTokenBucket
from app.workflows.contexts import Contexts, UserContext
from app.workflows.deps import WorkflowDeps, utcnow
from app.workflows.runner import RunStateError, UnknownWorkflow


@dataclass
class Services:
    settings: Settings
    session_factory: async_sessionmaker[AsyncSession]
    contexts: Contexts
    clock: Callable[[], datetime] = utcnow
    # Architecture §9: at most 5 password sign-in attempts a minute (one process).
    login_limiter: RateLimiter = field(default_factory=lambda: RateLimiter(5, 60))
    # Demo only: how often visitors may open a session or reset the shared workspace.
    demo_limiter: RateLimiter = field(default_factory=lambda: RateLimiter(30, 60))
    reset_limiter: RateLimiter = field(default_factory=lambda: RateLimiter(2, 60))
    oauth_limiter: RateLimiter = field(default_factory=lambda: RateLimiter(30, 60))
    google: GoogleOIDC | None = None  # set when Google sign-in is configured

    async def owner_context(self) -> UserContext | None:
        """The owner account's context: scheduled jobs and scripts act as the owner."""
        async with self.session_factory() as db:
            owner_id = await db.scalar(select(User.id).where(User.is_owner))
        return self.contexts.for_user(owner_id, is_owner=True) if owner_id else None


async def build_demo_services(settings: Settings, stack: AsyncExitStack) -> Services:
    """The public demo: same app, in-memory sample workspace, in-memory run checkpoints."""
    ensure_demo_database(settings.database_url)
    engine = make_engine(settings.database_url)
    stack.push_async_callback(engine.dispose)
    session_factory = make_session_factory(engine)
    make_deps = partial(
        WorkflowDeps,
        session_factory,
        demo_notion(),
        demo_specs(settings.notion_mapping_path),
        build_router(settings),
        writes_enabled=True,  # writes go to the in-memory workspace
        tz=ZoneInfo(settings.timezone),
    )
    contexts = Contexts(lambda user_id: make_deps(user_id=user_id), InMemorySaver())
    user = await demo_user(session_factory)
    context = contexts.for_user(user.id, is_owner=True)
    assert context is not None
    await reset_demo(context.deps)
    return Services(settings, session_factory, contexts)


async def build_services(settings: Settings, stack: AsyncExitStack) -> Services:
    if settings.lifeos_demo_mode:
        return await build_demo_services(settings, stack)
    if settings.notion_token is None:
        raise RuntimeError("NOTION_TOKEN is not set")
    client = await stack.enter_async_context(
        NotionClient(
            settings.notion_token.get_secret_value(),
            base_url=settings.notion_base_url,
            notion_version=settings.notion_version,
            rate_limiter=AsyncTokenBucket(rate=settings.notion_requests_per_second),
        )
    )
    specs = load_mapping(settings.notion_mapping_path, env_values(), settings.notion_profile)
    engine = make_engine(settings.database_url)
    stack.push_async_callback(engine.dispose)
    saver = await stack.enter_async_context(
        AsyncPostgresSaver.from_conn_string(settings.checkpoint_url)
    )
    await saver.setup()  # creates the checkpoint tables on first run
    session_factory = make_session_factory(engine)
    make_deps = partial(
        WorkflowDeps,
        session_factory,
        client,
        specs,
        build_router(settings),
        writes_enabled=settings.lifeos_writes_enabled,
        tz=ZoneInfo(settings.timezone),
    )
    contexts = Contexts(lambda user_id: make_deps(user_id=user_id), saver)
    return Services(settings, session_factory, contexts, google=google_client(settings))


def google_client(settings: Settings) -> GoogleOIDC | None:
    if not settings.google_sign_in:
        return None
    assert settings.google_client_id and settings.google_client_secret
    return GoogleOIDC(
        settings.google_client_id,
        settings.google_client_secret.get_secret_value(),
        f"{settings.lifeos_public_url.rstrip('/')}/api/v1/auth/google/callback",
    )


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    async with AsyncExitStack() as stack:
        app.state.services = await build_services(get_settings(), stack)
        yield


def create_app(services: Services | None = None) -> FastAPI:
    settings = services.settings if services else get_settings()
    # Production doesn't publish the OpenAPI schema or the interactive docs.
    hidden = {"docs_url": None, "redoc_url": None, "openapi_url": None}
    docs = hidden if settings.production else {}
    app = FastAPI(title="LifeOS", version="0.8.0", lifespan=None if services else _lifespan, **docs)
    if services:
        app.state.services = services
    app.include_router(router, prefix="/api/v1")
    app.include_router(cron_router, prefix="/api/v1")

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(error_body(exc.code, exc.message, exc.retryable), exc.status)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(error_body("http_error", str(exc.detail)), exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            error_body("invalid_request", f"{len(exc.errors())} invalid fields"), 422
        )

    @app.exception_handler(UnknownWorkflow)
    async def _unknown(request: Request, exc: UnknownWorkflow) -> JSONResponse:
        return JSONResponse(error_body("unknown_workflow", f"no workflow {exc}"), 404)

    @app.exception_handler(RunStateError)
    async def _run_state(request: Request, exc: RunStateError) -> JSONResponse:
        return JSONResponse(error_body("run_state", str(exc)), 409)

    @app.exception_handler(NotionError)
    async def _notion(request: Request, exc: NotionError) -> JSONResponse:
        code = f"notion_{exc.code}" if exc.code else "notion_error"
        body = error_body(code, str(exc), exc.retryable, exc.request_id)
        return JSONResponse(body, 502)

    return app


app = create_app()
