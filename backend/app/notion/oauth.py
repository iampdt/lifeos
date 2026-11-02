"""Notion's OAuth for public integrations (multi-user §Notion connection).

Authorisation code flow: the user picks pages (or duplicates a template) on Notion's consent
page, Notion redirects back with a code, and the code is exchanged for an access token and a
refresh token. Refreshing returns a new pair and invalidates the old refresh token, so callers
must store the new one before using the new access token (see `connections.py`).

The token endpoints take HTTP Basic authentication with the integration's client ID and secret.
The authorising user's details in the response (`owner`) are not kept.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

from app.notion.client import DEFAULT_BASE_URL, DEFAULT_VERSION


class NotionOAuthError(Exception):
    """The OAuth exchange, refresh or revocation failed. `code` is safe to show in a URL.
    `revoked` means the grant itself is no longer valid, so only reconnecting helps."""

    def __init__(self, code: str, message: str, *, revoked: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.revoked = revoked


@dataclass(frozen=True)
class TokenGrant:
    access_token: str
    refresh_token: str | None
    expires_in: int | None
    bot_id: str
    workspace_id: str
    workspace_name: str | None
    workspace_icon: str | None
    duplicated_template_id: str | None

    @classmethod
    def from_response(cls, body: dict[str, Any]) -> TokenGrant:
        try:
            expires_in = body.get("expires_in")
            return cls(
                access_token=body["access_token"],
                refresh_token=body.get("refresh_token"),
                expires_in=int(expires_in) if expires_in else None,
                bot_id=body["bot_id"],
                workspace_id=body["workspace_id"],
                workspace_name=body.get("workspace_name"),
                workspace_icon=body.get("workspace_icon"),
                duplicated_template_id=body.get("duplicated_template_id"),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise NotionOAuthError("exchange_failed", f"unexpected token response: {exc}") from exc


class NotionOAuth:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        notion_version: str = DEFAULT_VERSION,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self.client_id = client_id
        self.redirect_uri = redirect_uri
        self.base_url = base_url.rstrip("/")
        self._auth = httpx.BasicAuth(client_id, client_secret)
        self._headers = {"Notion-Version": notion_version, "Accept": "application/json"}
        self.http = http or httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0))

    def authorization_url(self, *, state: str) -> str:
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "owner": "user",
            "state": state,
        }
        return f"{self.base_url}/oauth/authorize?{urlencode(params)}"

    async def exchange(self, code: str) -> TokenGrant:
        body = {"grant_type": "authorization_code", "code": code, "redirect_uri": self.redirect_uri}
        return TokenGrant.from_response(await self._post("oauth/token", body, "exchange_failed"))

    async def refresh(self, refresh_token: str) -> TokenGrant:
        body = {"grant_type": "refresh_token", "refresh_token": refresh_token}
        return TokenGrant.from_response(await self._post("oauth/token", body, "refresh_failed"))

    async def revoke(self, access_token: str) -> None:
        await self._post("oauth/revoke", {"token": access_token}, "revoke_failed")

    async def _post(self, path: str, body: dict[str, Any], code: str) -> dict[str, Any]:
        try:
            response = await self.http.post(
                f"{self.base_url}/{path}", json=body, auth=self._auth, headers=self._headers
            )
        except httpx.HTTPError as exc:
            raise NotionOAuthError(code, f"couldn't reach Notion: {exc!r}") from exc
        if response.status_code == 200:
            return response.json()
        try:
            error = response.json()
        except ValueError:
            error = {}
        reason = error.get("error") or error.get("code") or f"HTTP {response.status_code}"
        # invalid_grant: the code or refresh token was used, expired or revoked.
        revoked = reason in ("invalid_grant", "unauthorized_client")
        raise NotionOAuthError(code, f"Notion refused the request: {reason}", revoked=revoked)
