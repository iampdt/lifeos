"""Encryption at rest for stored credentials (multi-user §Notion connection).

Fernet: AES-128-CBC with an HMAC-SHA256 tag, so a tampered or wrongly keyed value fails to
decrypt instead of returning garbage. `LIFEOS_ENCRYPTION_KEY` holds one or more comma-separated
keys: the first encrypts, every key decrypts, so a key can be rotated by putting the new one
first and re-encrypting at leisure.

    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken, MultiFernet


class DecryptionError(Exception):
    """The value was encrypted with an unknown key, or has been altered."""


class TokenCipher:
    def __init__(self, keys: str) -> None:
        parts = [key.strip() for key in keys.split(",") if key.strip()]
        if not parts:
            raise ValueError("LIFEOS_ENCRYPTION_KEY is empty")
        self._fernet = MultiFernet([Fernet(key) for key in parts])

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, token: str) -> str:
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except InvalidToken as exc:
            raise DecryptionError("stored credential can't be decrypted") from exc
