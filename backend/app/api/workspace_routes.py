"""An account's workspace: which data source and properties each module uses, and its rule
settings (multi-user §Generic workspace model).

The owner's workspace is configured in the environment and is read-only here. Any other account
saves a mapping for its connected Notion; it is checked structurally and then against the live
data sources before it is stored, so a saved mapping always matched the workspace when saved.
"""

from __future__ import annotations

from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert

from app.api.errors import ApiError
from app.api.security import CurrentUser, services
from app.db.models import NotionConnection, Workspace
from app.notion.codecs import plain_text
from app.notion.errors import NotionNotShared, NotionUnauthorized
from app.notion.schema import check_data_source
from app.workspaces.modules import MODULES, MappingIssue, validate_mapping
from app.workspaces.store import mapping_json, specs_from_mapping
from app.workspaces.suggest import (
    describe_schema,
    detect_date_format,
    suggest_module,
    suggest_sources,
)

router = APIRouter()


TIME = r"^([01]\d|2[0-3]):[0-5]\d$"


class WorkspaceSettings(BaseModel):
    timezone: str = Field(max_length=64)
    max_major_per_day: int = Field(3, ge=1, le=10)
    review_weekday: int = Field(6, ge=0, le=6)  # Monday is 0
    review_time: str = Field("18:00", pattern=TIME)
    morning_time: str = Field("07:30", pattern=TIME)
    evening_time: str = Field("21:00", pattern=TIME)
    schedule_enabled: bool = True

    @field_validator("timezone")
    @classmethod
    def _known_zone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown timezone {value!r}") from exc
        return value


class WorkspaceBody(BaseModel):
    modules: dict[str, dict[str, Any]]
    settings: WorkspaceSettings


@router.get("/workspace/modules")
async def modules(principal: CurrentUser) -> dict[str, Any]:
    """What each module needs, for the setup screen."""
    return {
        name: {
            "label": module.label,
            "required": module.required,
            "free_fields": module.free_fields,
            "fields": {
                field: {"types": sorted(rule.types), "required": rule.required,
                        "needs": list(rule.needs), "allows": list(rule.allows)}
                for field, rule in module.fields.items()
            },
        }
        for name, module in MODULES.items()
    }  # fmt: skip


@router.get("/workspace")
async def get_workspace(request: Request, principal: CurrentUser) -> dict[str, Any]:
    svc = services(request)
    async with svc.session_factory() as db:
        row = await db.get(Workspace, principal.user_id)
        connection = await db.get(NotionConnection, principal.user_id)
    connected = connection is not None and connection.status == "active"
    if principal.is_owner and row is None:
        return _environment_view(svc, can_import=connected and svc.environment_specs is not None)
    return {
        "source": "account" if row else None,
        "editable": svc.connections is not None,
        "modules": row.mapping if row else {},
        "settings": (
            {"timezone": row.timezone, "max_major_per_day": row.max_major_per_day,
             "review_weekday": row.review_weekday, "review_time": row.review_time,
             "morning_time": row.morning_time, "evening_time": row.evening_time,
             "schedule_enabled": row.schedule_enabled}
            if row else None
        ),
        # From the daily drift check: modules left out until the mapping is saved again.
        "paused": list(row.paused_modules or []) if row else [],
        "drift": row.drift if row else None,
        "checked_at": row.checked_at.isoformat() if row and row.checked_at else None,
        "can_import": False,
    }  # fmt: skip


def _environment_settings(svc) -> WorkspaceSettings:
    s = svc.settings
    return WorkspaceSettings(
        timezone=s.timezone, review_weekday=s.lifeos_review_weekday,
        review_time=s.lifeos_review_time, morning_time=s.lifeos_morning_time,
        evening_time=s.lifeos_evening_time,
    )  # fmt: skip


def _environment_view(svc, *, can_import: bool) -> dict[str, Any]:
    """The owner's workspace from the environment (read-only here). `can_import` says whether
    the owner's Notion connection could take it over."""
    return {
        "source": "environment" if svc.environment_specs else None,
        "editable": False,
        "modules": mapping_json(svc.environment_specs or {}),
        "settings": _environment_settings(svc).model_dump(),
        "paused": [],
        "drift": None,
        "checked_at": None,
        "can_import": can_import,
    }


@router.put("/workspace")
async def save_workspace(
    body: WorkspaceBody, request: Request, principal: CurrentUser
) -> dict[str, Any]:
    try:
        specs = specs_from_mapping(body.modules)
    except ValidationError as exc:
        problem = {"module": None, "field": None, "problem": str(exc)[:500]}
        raise ApiError(
            422, "invalid_mapping", "the mapping is malformed", details=[problem]
        ) from exc
    await _save(request, principal, specs, body.settings)
    return await get_workspace(request, principal)


@router.post("/workspace/import-environment")
async def import_environment(request: Request, principal: CurrentUser) -> dict[str, Any]:
    """The owner moves its environment workspace to its own Notion connection: the same mapping
    and routine times, checked against what the connection can see. From then on the owner's
    routines use the connection, and NOTION_TOKEN is no longer needed."""
    svc = services(request)
    if not principal.is_owner or svc.environment_specs is None:
        raise ApiError(404, "http_error", "Not Found")
    await _save(request, principal, dict(svc.environment_specs), _environment_settings(svc))
    return await get_workspace(request, principal)


async def _save(request: Request, principal, specs, settings: WorkspaceSettings) -> None:
    """Check a mapping structurally and against the live data sources, then store it."""
    svc = services(request)
    if svc.connections is None:
        raise ApiError(404, "http_error", "Not Found")
    async with svc.session_factory() as db:
        connection = await db.get(NotionConnection, principal.user_id)
    if connection is None or connection.status != "active":
        raise ApiError(409, "notion_reconnect", "connect Notion first")
    issues = validate_mapping(specs)
    if not issues:
        issues = await _live_issues(svc, principal.user_id, specs)
    if issues:
        raise ApiError(
            422, "invalid_mapping", "the mapping doesn't fit the workspace",
            details=[{"module": i.module, "field": i.field, "problem": i.problem} for i in issues],
        )  # fmt: skip
    values = {
        "user_id": principal.user_id,
        "mapping": mapping_json(specs),
        **settings.model_dump(),
        # Checked against the live workspace just now, so nothing is paused.
        "paused_modules": [],
        "drift": None,
        "checked_at": svc.clock(),
        "updated_at": func.now(),
    }
    async with svc.session_factory() as db, db.begin():
        await db.execute(
            insert(Workspace).values(**values).on_conflict_do_update(
                index_elements=["user_id"], set_=values
            )
        )  # fmt: skip
    await svc.contexts.forget(principal.user_id)


# --- onboarding suggestions -------------------------------------------------------------------

MAX_SOURCES = 200


async def _account_client(request: Request, principal):
    svc = services(request)
    if svc.connections is None:
        raise ApiError(404, "http_error", "Not Found")
    async with svc.session_factory() as db:
        connection = await db.get(NotionConnection, principal.user_id)
    if connection is None or connection.status != "active":
        raise ApiError(409, "notion_reconnect", "connect Notion first")
    return svc.connections.client(principal.user_id), connection


@router.get("/workspace/suggestion")
async def suggestion(request: Request, principal: CurrentUser) -> dict[str, Any]:
    """A first guess at the whole mapping, from the databases the connection can see."""
    client, connection = await _account_client(request, principal)
    try:
        sources: list[dict[str, str]] = []
        async for item in client.search_data_sources():
            sources.append({"id": item["id"], "title": plain_text(item.get("title")) or "Untitled"})
            if len(sources) == MAX_SOURCES:
                break
        modules: dict[str, Any] = {}
        schemas: dict[str, Any] = {}
        for module, source_id in suggest_sources(sources).items():
            if source_id:
                modules[module], schemas[source_id] = await _suggest_for(client, module, source_id)
    except NotionUnauthorized as exc:
        raise ApiError(409, "notion_reconnect", "connect Notion again to continue") from exc
    finally:
        await client.aclose()
    return {
        "data_sources": sources,
        "modules": modules,
        "schemas": schemas,
        "from_template": connection.duplicated_template_id is not None,
    }


@router.get("/workspace/data-sources/{source_id}")
async def data_source_suggestion(
    source_id: str, module: str, request: Request, principal: CurrentUser
) -> dict[str, Any]:
    """One database's properties, and a guess at mapping `module` onto it."""
    if module not in MODULES:
        raise ApiError(404, "unknown_module", f"no module {module!r}")
    client, _ = await _account_client(request, principal)
    try:
        suggested, schema = await _suggest_for(client, module, source_id)
    except NotionNotShared as exc:
        raise ApiError(404, "not_shared", "LifeOS can't see this database") from exc
    except NotionUnauthorized as exc:
        raise ApiError(409, "notion_reconnect", "connect Notion again to continue") from exc
    finally:
        await client.aclose()
    return {"suggestion": suggested, "schema": schema}


async def _suggest_for(client, module: str, source_id: str) -> tuple[dict, dict]:
    data_source = await client.get_data_source(source_id)
    properties = describe_schema(data_source)
    entry: dict[str, Any] = {
        "data_source_id": source_id,
        "database_id": (data_source.get("parent") or {}).get("database_id"),
        **suggest_module(module, properties),
    }
    if module == "habits" and "day" not in entry["properties"]:
        entry["date_format"] = detect_date_format(await _sample_titles(client, source_id))
    schema = {"title": plain_text(data_source.get("title")) or "Untitled", "properties": properties}
    return entry, schema


async def _sample_titles(client, source_id: str, limit: int = 5) -> list[str]:
    titles: list[str] = []
    async for page in client.query_data_source(source_id, page_size=limit):
        for prop in (page.get("properties") or {}).values():
            if prop.get("type") == "title":
                titles.append(plain_text(prop.get("title")))
        if len(titles) >= limit:
            break
    return titles


async def _live_issues(svc, user_id, specs) -> list[MappingIssue]:
    """Compare each module with its live data source: names, types and options."""
    assert svc.connections is not None
    client = svc.connections.client(user_id)
    issues: list[MappingIssue] = []
    try:
        for name, spec in specs.items():
            try:
                report = await check_data_source(client, spec)
            except NotionNotShared:
                issues.append(MappingIssue(name, None, "LifeOS can't see this database"))
                continue
            issues += [
                MappingIssue(name, i.field, f"{i.property_name!r}: {i.problem} {i.detail}".strip())
                for i in report.issues
                if i.blocking
            ]
    except NotionUnauthorized as exc:
        raise ApiError(409, "notion_reconnect", "connect Notion again to continue") from exc
    finally:
        await client.aclose()
    return issues
