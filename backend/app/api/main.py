"""FastAPI application: `uvicorn app.api.main:app` (design §12).

Services (Notion client, database, LLM router, LangGraph checkpointer) are built once in the
lifespan. Tests pass ready-made `Services` to `create_app` instead.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.errors import ApiError, error_body
from app.api.routes import router
from app.auth.sessions import RateLimiter
from app.core.config import Settings, env_values, get_settings
from app.db.session import make_engine, make_session_factory
from app.llm.factory import build_router
from app.notion.client import NotionClient
from app.notion.errors import NotionError
from app.notion.mapping import load_mapping
from app.notion.rate_limiter import AsyncTokenBucket
from app.workflows.deps import WorkflowDeps
from app.workflows.runner import RunStateError, UnknownWorkflow, WorkflowRunner


@dataclass
class Services:
    settings: Settings
    deps: WorkflowDeps
    runner: WorkflowRunner
    # §14: at most 5 sign-in attempts a minute (one process, one owner).
    login_limiter: RateLimiter = field(default_factory=lambda: RateLimiter(5, 60))


async def build_services(settings: Settings, stack: AsyncExitStack) -> Services:
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
    deps = WorkflowDeps(
        make_session_factory(engine),
        client,
        specs,
        build_router(settings),
        writes_enabled=settings.lifeos_writes_enabled,
        tz=ZoneInfo(settings.timezone),
    )
    return Services(settings, deps, WorkflowRunner(deps, saver))


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    async with AsyncExitStack() as stack:
        app.state.services = await build_services(get_settings(), stack)
        yield


def create_app(services: Services | None = None) -> FastAPI:
    app = FastAPI(title="LifeOS", version="0.4.0", lifespan=None if services else _lifespan)
    if services:
        app.state.services = services
    app.include_router(router, prefix="/api/v1")

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
