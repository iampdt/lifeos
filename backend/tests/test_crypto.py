"""Encryption of stored credentials: round trip, key rotation, wrong key, tampering."""

import pytest
from cryptography.fernet import Fernet

from app.core.crypto import DecryptionError, TokenCipher

OLD, NEW = Fernet.generate_key().decode(), Fernet.generate_key().decode()


def test_round_trip_and_ciphertext_hides_the_value():
    cipher = TokenCipher(OLD)
    sealed = cipher.encrypt("secret-token")
    assert "secret-token" not in sealed and cipher.decrypt(sealed) == "secret-token"
    assert cipher.encrypt("secret-token") != sealed  # random IV each time


def test_rotation_new_key_first_still_reads_old_values():
    sealed_with_old = TokenCipher(OLD).encrypt("value")
    rotated = TokenCipher(f"{NEW}, {OLD}")
    assert rotated.decrypt(sealed_with_old) == "value"
    assert TokenCipher(NEW).decrypt(rotated.encrypt("value")) == "value"


def test_wrong_key_or_tampering_fails_loudly():
    sealed = TokenCipher(OLD).encrypt("value")
    with pytest.raises(DecryptionError):
        TokenCipher(NEW).decrypt(sealed)
    with pytest.raises(DecryptionError):
        TokenCipher(OLD).decrypt(sealed[:-4] + "AAAA")
    with pytest.raises(ValueError):
        TokenCipher(" , ")
