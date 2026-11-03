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

from app.api.ai_routes import router as ai_router
from app.api.cron import router as cron_router
from app.api.errors import ApiError, error_body
from app.api.notion_connect import router as notion_connect_router
from app.api.pages import router as pages_router
from app.api.reports import router as reports_router
from app.api.routes import router
from app.api.schedule_routes import router as schedule_router
from app.api.workspace_routes import router as workspace_router
from app.auth.google import GoogleOIDC
from app.auth.sessions import RateLimiter
from app.core.config import Settings, env_values, get_settings
from app.core.crypto import TokenCipher
from app.db.models import User
from app.db.session import make_engine, make_session_factory
from app.demo.setup import demo_notion, demo_specs, demo_user, ensure_demo_database, reset_demo
from app.llm.access import AccountLLM, AIAccessService
from app.llm.factory import build_router, check_gemini_key, own_key_router
from app.llm.router import LLMRouter
from app.notion.client import NotionClient
from app.notion.connections import NotionConnections, ReconnectNeeded
from app.notion.errors import NotionError
from app.notion.mapping import DataSourceSpec, load_mapping
from app.notion.oauth import NotionOAuth
from app.notion.rate_limiter import AsyncTokenBucket
from app.permissions.modes import load_modes
from app.workflows.contexts import Contexts, UserContext
from app.workflows.deps import WorkflowDeps, utcnow
from app.workflows.runner import RunStateError, UnknownWorkflow
from app.workspaces.store import AccountWorkspaces


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
    connections: NotionConnections | None = None  # set when Notion OAuth is configured
    ai: AIAccessService | None = None  # accounts' AI access (not used by the owner or the demo)
    # The environment's mapping (configured modules), which the owner can move to its account.
    environment_specs: dict[str, DataSourceSpec] | None = None

    async def owner_context(self) -> UserContext | None:
        """The owner account's context: scheduled jobs and scripts act as the owner."""
        async with self.session_factory() as db:
            owner_id = await db.scalar(select(User.id).where(User.is_owner))
        return await self.contexts.for_user(owner_id, is_owner=True) if owner_id else None


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
    contexts = Contexts(
        lambda user_id: make_deps(user_id=user_id),
        InMemorySaver(),
        modes_for=partial(load_modes, session_factory),
    )
    user = await demo_user(session_factory)
    context = await contexts.for_user(user.id, is_owner=True)
    assert context is not None
    await reset_demo(context.deps)
    return Services(settings, session_factory, contexts)


async def build_services(settings: Settings, stack: AsyncExitStack) -> Services:
    if settings.lifeos_demo_mode:
        return await build_demo_services(settings, stack)
    # The environment's workspace (an internal integration) is optional once accounts, the
    # owner included, can connect their own Notion.
    if settings.notion_token is None and not settings.notion_oauth:
        raise RuntimeError("set NOTION_TOKEN, or configure Notion connections (NOTION_OAUTH_*)")
    client = None
    if settings.notion_token is not None:
        client = await stack.enter_async_context(
            NotionClient(
                settings.notion_token.get_secret_value(),
                base_url=settings.notion_base_url,
                notion_version=settings.notion_version,
                rate_limiter=AsyncTokenBucket(rate=settings.notion_requests_per_second),
            )
        )
    specs = load_mapping(settings.notion_mapping_path, env_values(), settings.notion_profile)
    environment = client is not None and specs["tasks"].configured
    engine = make_engine(settings.database_url)
    stack.push_async_callback(engine.dispose)
    saver = await stack.enter_async_context(
        AsyncPostgresSaver.from_conn_string(settings.checkpoint_url)
    )
    await saver.setup()  # creates the checkpoint tables on first run
    session_factory = make_session_factory(engine)
    llm = build_router(settings)
    make_deps = partial(
        WorkflowDeps,
        session_factory,
        client,
        specs,
        llm,
        writes_enabled=settings.lifeos_writes_enabled,
        tz=ZoneInfo(settings.timezone),
    )
    connections = notion_connections(settings, session_factory)
    ai = ai_access(settings, session_factory, llm)
    accounts = None
    if connections is not None:

        def account_deps(notion, account_specs, user_id, tz, rules, is_owner) -> WorkflowDeps:
            # The owner uses the service's key directly; other accounts go through AI access.
            model = llm if is_owner else AccountLLM(user_id, ai)
            return WorkflowDeps(
                session_factory, notion, account_specs, model, user_id=user_id, tz=tz,
                rules=rules, writes_enabled=settings.lifeos_writes_enabled,
                workspace_source="account",
            )  # fmt: skip

        accounts = AccountWorkspaces(session_factory, connections, account_deps)
    contexts = Contexts(
        lambda user_id: make_deps(user_id=user_id) if environment else None,
        saver,
        account_deps=accounts.deps_for if accounts else None,
        modes_for=partial(load_modes, session_factory),
    )
    return Services(
        settings,
        session_factory,
        contexts,
        google=google_client(settings),
        connections=connections,
        ai=ai,
        environment_specs={n: s for n, s in specs.items() if s.configured} if environment else None,
    )


def google_client(settings: Settings) -> GoogleOIDC | None:
    if not settings.google_sign_in:
        return None
    assert settings.google_client_id and settings.google_client_secret
    return GoogleOIDC(
        settings.google_client_id,
        settings.google_client_secret.get_secret_value(),
        f"{settings.lifeos_public_url.rstrip('/')}/api/v1/auth/google/callback",
    )


def ai_access(
    settings: Settings, session_factory: async_sessionmaker[AsyncSession], service: LLMRouter
) -> AIAccessService:
    key = settings.lifeos_encryption_key
    return AIAccessService(
        session_factory,
        service,
        allowance=settings.llm_user_daily_tokens,
        cipher=TokenCipher(key.get_secret_value()) if key else None,
        own_router=partial(own_key_router, settings),
        check_key=partial(check_gemini_key, settings),
    )


def notion_connections(
    settings: Settings, session_factory: async_sessionmaker[AsyncSession]
) -> NotionConnections | None:
    if not settings.notion_oauth:
        return None
    assert settings.notion_oauth_client_id and settings.notion_oauth_client_secret
    assert settings.lifeos_encryption_key and settings.lifeos_public_url
    oauth = NotionOAuth(
        settings.notion_oauth_client_id,
        settings.notion_oauth_client_secret.get_secret_value(),
        f"{settings.lifeos_public_url.rstrip('/')}/api/v1/notion/callback",
        base_url=settings.notion_base_url,
        notion_version=settings.notion_version,
    )
    return NotionConnections(
        session_factory,
        TokenCipher(settings.lifeos_encryption_key.get_secret_value()),
        oauth,
        base_url=settings.notion_base_url,
        notion_version=settings.notion_version,
        requests_per_second=settings.notion_requests_per_second,
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
    app.include_router(notion_connect_router, prefix="/api/v1")
    app.include_router(workspace_router, prefix="/api/v1")
    app.include_router(ai_router, prefix="/api/v1")
    app.include_router(schedule_router, prefix="/api/v1")
    app.include_router(reports_router, prefix="/api/v1")
    app.include_router(pages_router, prefix="/api/v1")

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        body = error_body(exc.code, exc.message, exc.retryable, details=exc.details)
        return JSONResponse(body, exc.status)

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
        if isinstance(exc, ReconnectNeeded):
            return JSONResponse(error_body("notion_reconnect", str(exc)), 409)
        code = f"notion_{exc.code}" if exc.code else "notion_error"
        body = error_body(code, str(exc), exc.retryable, exc.request_id)
        return JSONResponse(body, 502)

    return app


app = create_app()
