"""Unit tests for the per-user secret vault (AES-256-GCM round-trip)."""

from __future__ import annotations

import base64
import os

import pytest

from app.config import get_settings


def _set_key(monkeypatch: pytest.MonkeyPatch, key_bytes: bytes) -> None:
    monkeypatch.setenv("SECRET_VAULT_KEY", base64.urlsafe_b64encode(key_bytes).decode())
    get_settings.cache_clear()


def test_encrypt_decrypt_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.auth import vault

    _set_key(monkeypatch, os.urandom(32))
    secret = "delta-read-key-Abc123!@#"
    ciphertext, nonce = vault.encrypt(secret)

    assert secret.encode() not in ciphertext  # not stored in the clear
    assert len(nonce) == 12
    assert vault.decrypt(ciphertext, nonce) == secret


def test_fresh_nonce_each_call(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.auth import vault

    _set_key(monkeypatch, os.urandom(32))
    c1, n1 = vault.encrypt("same")
    c2, n2 = vault.encrypt("same")
    assert n1 != n2 and c1 != c2  # GCM with random nonce => different ciphertexts


def test_wrong_key_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    from cryptography.exceptions import InvalidTag

    from app.auth import vault

    _set_key(monkeypatch, os.urandom(32))
    ciphertext, nonce = vault.encrypt("top-secret")

    _set_key(monkeypatch, os.urandom(32))  # rotate to a different key
    with pytest.raises(InvalidTag):
        vault.decrypt(ciphertext, nonce)


def test_bad_key_length_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.auth import vault

    _set_key(monkeypatch, os.urandom(16))  # AES-128 length, not allowed
    with pytest.raises(RuntimeError, match="32 bytes"):
        vault.encrypt("x")


def test_missing_key_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.auth import vault

    monkeypatch.setenv("SECRET_VAULT_KEY", "")
    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="not configured"):
        vault.encrypt("x")


def teardown_module() -> None:
    # Don't leak a cached test Settings into other test modules.
    get_settings.cache_clear()
