import pytest

from app.auth.passwords import hash_password, needs_rehash, verify_password
from app.auth.sessions import RateLimiter, normalize_email, token_hash


def test_passwords_are_argon2id_and_verified():
    hashed = hash_password("correct horse battery")
    assert hashed.startswith("$argon2id$") and not needs_rehash(hashed)
    assert verify_password(hashed, "correct horse battery")
    assert not verify_password(hashed, "wrong password!!")
    assert not verify_password(None, "lifeos-dummy-password")  # unknown account never passes
    assert not verify_password("not-a-hash", "whatever")
    with pytest.raises(ValueError):
        hash_password("short")


def test_rate_limiter_sliding_window():
    now = [0.0]
    limiter = RateLimiter(2, 60, clock=lambda: now[0])
    assert limiter.allow("k") and limiter.allow("k") and not limiter.allow("k")
    assert limiter.allow("other")
    now[0] = 61
    assert limiter.allow("k")


def test_helpers():
    assert normalize_email("  Me@Example.COM ") == "me@example.com"
    assert len(token_hash("x")) == 64 and token_hash("x") != token_hash("y")
