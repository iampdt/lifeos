"""Sign in with Google against a fake Google: state, PKCE, ID-token checks and account linking."""

import base64
import hashlib
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import select, update

from app.api.main import create_app
from app.auth.google import JWKS_URL, TOKEN_URL, GoogleOIDC
from app.core.config import Settings
from app.db.models import AuditLog, OAuthState, User
from app.llm.mock import MockLLM
from tests.fake_notion import FakeNotion
from tests.workflow_support import make_services, set_password

CLIENT_ID = "client-id.apps.googleusercontent.com"
REDIRECT_URI = "https://app.test/api/v1/auth/google/callback"
OWNER = "owner@example.com"
NOW = datetime(2026, 11, 1, 15, 0, tzinfo=UTC)


class FakeGoogle:
    """Google's token and key endpoints. Issues an ID token for whatever claims a test sets."""

    def __init__(self) -> None:
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.claims: dict = {}
        self.signing_key = self.key
        self.challenge = ""

    def jwks(self) -> dict:
        jwk = jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key(), as_dict=True)
        return {"keys": [{**jwk, "kid": "k1", "alg": "RS256", "use": "sig"}]}

    def id_token(self) -> str:
        now = int(time.time())
        claims = {"iss": "https://accounts.google.com", "aud": CLIENT_ID, "iat": now,
                  "exp": now + 3600, "email_verified": True, **self.claims}  # fmt: skip
        return jwt.encode(claims, self.signing_key, algorithm="RS256", headers={"kid": "k1"})

    def handler(self, request: httpx.Request) -> httpx.Response:
        if str(request.url) == JWKS_URL:
            return httpx.Response(200, json=self.jwks())
        assert str(request.url) == TOKEN_URL
        form = parse_qs(request.content.decode())
        verifier = form["code_verifier"][0]
        digest = hashlib.sha256(verifier.encode()).digest()
        if base64.urlsafe_b64encode(digest).rstrip(b"=").decode() != self.challenge:
            return httpx.Response(400, json={"error": "invalid_grant"})
        assert form["redirect_uri"] == [REDIRECT_URI] and form["code"] == ["the-code"]
        return httpx.Response(200, json={"id_token": self.id_token()})


class Env:
    def __init__(self, client, google, session_factory):
        self.client, self.google, self.session_factory = client, google, session_factory

    async def start(self) -> dict[str, str]:
        """Begin a sign-in; returns the parameters sent to Google's consent page."""
        response = await self.client.get("/api/v1/auth/google/start")
        assert response.status_code == 302
        url = urlsplit(response.headers["location"])
        assert url.netloc == "accounts.google.com"
        params = {key: values[0] for key, values in parse_qs(url.query).items()}
        self.google.challenge = params["code_challenge"]
        return params

    async def sign_in(self, **claims) -> httpx.Response:
        params = await self.start()
        self.google.claims = {"nonce": params["nonce"], **claims}
        return await self.client.get(
            "/api/v1/auth/google/callback", params={"code": "the-code", "state": params["state"]}
        )


def make_env(session_factory, **settings):
    google = FakeGoogle()
    config = Settings(
        _env_file=None,
        google_client_id=CLIENT_ID,
        google_client_secret="client-secret",
        lifeos_public_url="https://app.test/",
        **settings,
    )
    oidc = GoogleOIDC(
        CLIENT_ID,
        "client-secret",
        REDIRECT_URI,
        http=httpx.AsyncClient(transport=httpx.MockTransport(google.handler)),
    )
    services, _ = make_services(
        session_factory, FakeNotion(), MockLLM(), now=NOW, settings=config, google=oidc
    )
    app = create_app(services)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test")
    return Env(client, google, session_factory)


@pytest.fixture
async def env(session_factory):
    await set_password(session_factory, "a long test password")
    env = make_env(session_factory)
    async with env.client:
        yield env


@pytest.fixture
async def open_env(session_factory):
    env = make_env(session_factory, lifeos_open_signup=True)
    async with env.client:
        yield env


def error_of(response: httpx.Response) -> str:
    assert response.status_code == 302
    return parse_qs(urlsplit(response.headers["location"]).query)["error"][0]


async def test_the_consent_request_uses_pkce_state_nonce_and_basic_scopes(env):
    params = await env.start()
    assert params["client_id"] == CLIENT_ID and params["redirect_uri"] == REDIRECT_URI
    assert params["scope"] == "openid email profile" and params["response_type"] == "code"
    assert params["code_challenge_method"] == "S256" and len(params["code_challenge"]) == 43
    assert params["state"] != params["nonce"]
    async with env.session_factory() as db:
        attempt = await db.get(OAuthState, params["state"])
    assert attempt is not None and attempt.provider == "google"


async def test_the_existing_owner_is_linked_by_email_and_signed_in(env):
    response = await env.sign_in(sub="google-123", email="Owner@Example.com", name="Owner Name")
    assert response.status_code == 302 and response.headers["location"] == "/"
    cookie = response.headers["set-cookie"].lower()
    assert "lifeos_session=" in cookie and "httponly" in cookie and "secure" in cookie
    me = (await env.client.get("/api/v1/auth/me")).json()
    assert me["email"] == OWNER and me["via"] == "session" and me["csrf_token"]
    async with env.session_factory() as db:
        users = (await db.scalars(select(User))).all()
        events = (await db.scalars(select(AuditLog.event))).all()
    assert len(users) == 1 and users[0].google_sub == "google-123" and users[0].name == "Owner Name"
    assert "auth.google_linked" in events

    # Later sign-ins find the account by Google's subject ID, even if the email changed.
    env.client.cookies.clear()
    again = await env.sign_in(sub="google-123", email="renamed@example.com")
    assert again.headers["location"] == "/"
    assert (await env.client.get("/api/v1/auth/me")).json()["email"] == OWNER


async def test_a_sign_in_attempt_is_single_use(env):
    params = await env.start()
    env.google.claims = {"sub": "google-123", "email": OWNER, "nonce": params["nonce"]}
    query = {"code": "the-code", "state": params["state"]}
    first = await env.client.get("/api/v1/auth/google/callback", params=query)
    assert first.headers["location"] == "/"
    env.client.cookies.clear()
    replay = await env.client.get("/api/v1/auth/google/callback", params=query)
    assert error_of(replay) == "expired" and "set-cookie" not in replay.headers


async def test_an_unknown_or_expired_state_is_refused(env):
    unknown = await env.client.get(
        "/api/v1/auth/google/callback", params={"code": "the-code", "state": "made-up"}
    )
    assert error_of(unknown) == "expired"

    params = await env.start()
    env.google.claims = {"sub": "google-123", "email": OWNER, "nonce": params["nonce"]}
    async with env.session_factory() as db, db.begin():
        await db.execute(
            update(OAuthState)
            .where(OAuthState.state == params["state"])
            .values(expires_at=NOW - timedelta(minutes=1))
        )
    late = await env.client.get(
        "/api/v1/auth/google/callback", params={"code": "the-code", "state": params["state"]}
    )
    assert error_of(late) == "expired"


async def test_cancelling_on_googles_page_returns_to_sign_in(env):
    params = await env.start()
    response = await env.client.get(
        "/api/v1/auth/google/callback", params={"error": "access_denied", "state": params["state"]}
    )
    assert error_of(response) == "cancelled"


@pytest.mark.parametrize(
    ("claims", "code"),
    [
        ({"nonce": "replayed-nonce"}, "invalid_token"),
        ({"aud": "someone-elses-client"}, "invalid_token"),
        ({"iss": "https://evil.example.com"}, "invalid_token"),
        ({"exp": int(time.time()) - 3600}, "invalid_token"),
        ({"email_verified": False}, "email_unverified"),
    ],
)
async def test_id_tokens_that_fail_a_check_are_refused(env, claims, code):
    params = await env.start()
    env.google.claims = {"sub": "google-123", "email": OWNER, "nonce": params["nonce"], **claims}
    response = await env.client.get(
        "/api/v1/auth/google/callback", params={"code": "the-code", "state": params["state"]}
    )
    assert error_of(response) == code and "set-cookie" not in response.headers
    async with env.session_factory() as db:
        assert (await db.scalar(select(User.google_sub))) is None


async def test_a_token_signed_by_another_key_is_refused(env):
    env.google.signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    response = await env.sign_in(sub="google-123", email=OWNER)
    assert error_of(response) == "invalid_token"


async def test_a_failed_code_exchange_is_refused(env):
    params = await env.start()
    env.google.challenge = "not-the-challenge"  # the verifier no longer matches
    env.google.claims = {"sub": "google-123", "email": OWNER, "nonce": params["nonce"]}
    response = await env.client.get(
        "/api/v1/auth/google/callback", params={"code": "the-code", "state": params["state"]}
    )
    assert error_of(response) == "exchange_failed"


async def test_new_accounts_wait_for_open_sign_up(env):
    response = await env.sign_in(sub="google-999", email="new@example.com")
    assert error_of(response) == "signup_closed"
    async with env.session_factory() as db:
        assert len((await db.scalars(select(User))).all()) == 1


async def test_open_sign_up_creates_an_account_without_a_password(open_env):
    response = await open_env.sign_in(sub="google-999", email="new@example.com", name="New User")
    assert response.headers["location"] == "/"
    assert (await open_env.client.get("/api/v1/auth/me")).json()["email"] == "new@example.com"
    async with open_env.session_factory() as db:
        user = await db.scalar(select(User).where(User.email == "new@example.com"))
    assert not user.is_owner and user.google_sub == "google-999" and user.password_hash is None


async def test_an_email_linked_to_another_google_account_is_refused(env):
    first = await env.sign_in(sub="google-123", email=OWNER)
    assert first.headers["location"] == "/"
    env.client.cookies.clear()
    other = await env.sign_in(sub="google-456", email=OWNER)
    assert error_of(other) == "account_conflict"


async def test_password_sign_in_is_off_once_google_is_configured(env):
    meta = (await env.client.get("/api/v1/meta")).json()
    assert meta == {"demo": False, "google": True, "password": False, "contact": None}
    response = await env.client.post(
        "/api/v1/auth/login", json={"email": OWNER, "password": "a long test password"}
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "password_sign_in_disabled"


async def test_google_routes_do_not_exist_without_configuration(session_factory):
    services, _ = make_services(session_factory, FakeNotion(), MockLLM(), now=NOW)
    app = create_app(services)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
        assert (await client.get("/api/v1/auth/google/start")).status_code == 404
        assert (await client.get("/api/v1/auth/google/callback")).status_code == 404
        meta = (await client.get("/api/v1/meta")).json()
    assert meta == {"demo": False, "google": False, "password": True, "contact": None}
