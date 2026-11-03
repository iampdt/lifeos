"""An account's AI access: agreeing to the free-tier terms, and its own Gemini key
(multi-user §AI under free tier). The deployment owner uses the service's key and has nothing
to set here.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.api.errors import ApiError
from app.api.security import CurrentUser, Principal, services
from app.llm.access import DISCLOSURE, DISCLOSURE_VERSION, AIAccessService, AIStatus
from app.llm.base import LLMKeyRejected, LLMUnavailable

router = APIRouter()


class ConsentBody(BaseModel):
    version: str = Field(max_length=16, description="The disclosure version that was shown")


class KeyBody(BaseModel):
    key: str = Field(min_length=20, max_length=200)


def _view(status: AIStatus) -> dict[str, Any]:
    return {
        "mode": status.mode,  # own_key | allowance | allowance_used | off
        "service_available": status.service_available,
        "consent": {
            "given": status.consent_current,
            "outdated": status.consented and not status.consent_current,
            "version": DISCLOSURE_VERSION,
            "text": DISCLOSURE,
        },
        "allowance": {"tokens": status.allowance, "used_today": status.used_today},
        "own_key": {
            "available": status.own_keys,
            "hint": status.key_hint,
            "status": status.key_status,
            "tokens_today": status.own_tokens_today,
        },
    }


def _access(request: Request, principal: Principal) -> AIAccessService:
    ai = services(request).ai
    if principal.is_owner or ai is None:
        raise ApiError(403, "deployment_key", "this account uses the deployment's own key")
    return ai


@router.get("/ai")
async def ai_status(request: Request, principal: CurrentUser) -> dict[str, Any]:
    ai = services(request).ai
    if principal.is_owner or ai is None:
        return {"mode": "deployment"}
    return _view(await ai.status(principal.user_id))


@router.post("/ai/consent")
async def consent(body: ConsentBody, request: Request, principal: CurrentUser) -> dict[str, Any]:
    ai = _access(request, principal)
    if body.version != DISCLOSURE_VERSION:
        raise ApiError(409, "disclosure_changed", "the terms changed; read them again")
    await ai.consent(principal.user_id)
    return _view(await ai.status(principal.user_id))


@router.post("/ai/consent/withdraw")
async def withdraw(request: Request, principal: CurrentUser) -> dict[str, Any]:
    ai = _access(request, principal)
    await ai.withdraw(principal.user_id)
    return _view(await ai.status(principal.user_id))


@router.put("/ai/key")
async def add_key(body: KeyBody, request: Request, principal: CurrentUser) -> dict[str, Any]:
    svc = services(request)
    ai = _access(request, principal)
    if principal.via != "session":
        raise ApiError(403, "session_required", "add a key from a signed-in browser")
    if ai.cipher is None:
        raise ApiError(404, "own_keys_unavailable", "this deployment can't store keys")
    if not svc.oauth_limiter.allow(f"aikey:{principal.user_id}"):
        raise ApiError(429, "rate_limited", "too many attempts; wait a minute", True)
    try:
        await ai.set_key(principal.user_id, body.key.strip())
    except LLMKeyRejected as exc:
        raise ApiError(422, "key_rejected", "Google didn't accept this key") from exc
    except LLMUnavailable as exc:
        raise ApiError(
            503, "key_check_failed", "couldn't reach Google to check the key", True
        ) from exc
    return _view(await ai.status(principal.user_id))


@router.delete("/ai/key")
async def remove_key(request: Request, principal: CurrentUser) -> dict[str, Any]:
    ai = _access(request, principal)
    await ai.remove_key(principal.user_id)
    return _view(await ai.status(principal.user_id))
