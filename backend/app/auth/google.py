"""Sign in with Google: OpenID Connect authorisation code flow with PKCE (multi-user §Accounts).

Only the `openid email profile` scopes are requested, so the app needs no Google verification.
The ID token is verified locally against Google's published signing keys: signature, issuer,
audience, expiry, nonce, and a verified email.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
ISSUERS = ("https://accounts.google.com", "accounts.google.com")
SCOPES = "openid email profile"
KEYS_TTL_SECONDS = 3600


class GoogleSignInError(Exception):
    """The sign-in can't be completed. `code` is safe to show in a URL."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class GoogleIdentity:
    sub: str
    email: str
    name: str | None


def new_pkce() -> tuple[str, str]:
    """(code_verifier, code_challenge) for the S256 method."""
    verifier = secrets.token_urlsafe(64)[:96]
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


class GoogleOIDC:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        *,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.http = http or httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0))
        self._keys: dict[str, Any] = {}
        self._keys_fetched = 0.0

    def authorization_url(self, *, state: str, nonce: str, code_challenge: str) -> str:
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": SCOPES,
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "prompt": "select_account",
        }
        return f"{AUTHORIZE_URL}?{urlencode(params)}"

    async def exchange(self, code: str, code_verifier: str) -> str:
        """Trade the authorisation code for tokens; returns the ID token."""
        response = await self.http.post(
            TOKEN_URL,
            data={
                "code": code,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "redirect_uri": self.redirect_uri,
                "grant_type": "authorization_code",
                "code_verifier": code_verifier,
            },
        )
        if response.status_code != 200:
            raise GoogleSignInError(
                "exchange_failed", f"token endpoint returned {response.status_code}"
            )
        id_token = response.json().get("id_token")
        if not id_token:
            raise GoogleSignInError("exchange_failed", "no ID token in the response")
        return id_token

    async def _signing_key(self, kid: str) -> Any:
        stale = time.monotonic() - self._keys_fetched > KEYS_TTL_SECONDS
        if stale or kid not in self._keys:
            response = await self.http.get(JWKS_URL)
            response.raise_for_status()
            self._keys = {key["kid"]: jwt.PyJWK(key).key for key in response.json().get("keys", [])}
            self._keys_fetched = time.monotonic()
        if kid not in self._keys:
            raise GoogleSignInError("invalid_token", "unknown signing key")
        return self._keys[kid]

    async def verify(self, id_token: str, *, nonce: str) -> GoogleIdentity:
        try:
            kid = jwt.get_unverified_header(id_token).get("kid", "")
            claims = jwt.decode(
                id_token,
                await self._signing_key(kid),
                algorithms=["RS256"],
                audience=self.client_id,
                issuer=ISSUERS,
                leeway=60,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
        except jwt.PyJWTError as exc:
            raise GoogleSignInError("invalid_token", f"ID token rejected: {exc}") from exc
        if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
            raise GoogleSignInError("invalid_token", "nonce mismatch")
        if not claims.get("email") or claims.get("email_verified") is not True:
            raise GoogleSignInError("email_unverified", "the Google account's email isn't verified")
        return GoogleIdentity(sub=claims["sub"], email=claims["email"], name=claims.get("name"))
