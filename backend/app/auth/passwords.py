"""Password hashing with argon2id (argon2-cffi defaults, RFC 9106 low-memory profile)."""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()
# Verifying against this when the email is unknown keeps both failure paths equally slow, so
# response time doesn't reveal whether an account exists.
_DUMMY_HASH = _hasher.hash("lifeos-dummy-password")

MIN_LENGTH = 12


def hash_password(password: str) -> str:
    if len(password) < MIN_LENGTH:
        raise ValueError(f"use at least {MIN_LENGTH} characters")
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)
