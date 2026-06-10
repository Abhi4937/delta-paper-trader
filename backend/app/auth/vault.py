"""Per-user secret vault — AES-256-GCM encryption at rest for Delta credentials.

Plaintext NEVER leaves the server. The master key comes from SECRET_VAULT_KEY
(urlsafe-base64 encoding of 32 random bytes). Each stored secret keeps its own random
12-byte nonce alongside the ciphertext.

The trade key/secret slots are STORED here but are only ever USED by the ring-fenced
live-execution module (sub-project C) — never by the paper-sim or market-data paths.
"""

from __future__ import annotations

import base64
import os
import uuid

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import UserSecret

# The allowed secret slots a user may store. Anything else is rejected.
SECRET_KINDS: tuple[str, ...] = (
    "delta_read_key",
    "delta_read_secret",
    "delta_trade_key",
    "delta_trade_secret",
    "delta_web_jwt",
)


def _master_key() -> bytes:
    raw = get_settings().secret_vault_key
    if not raw:
        raise RuntimeError("SECRET_VAULT_KEY is not configured")
    key = base64.urlsafe_b64decode(raw)
    if len(key) != 32:
        raise RuntimeError("SECRET_VAULT_KEY must decode to 32 bytes (AES-256)")
    return key


def encrypt(plaintext: str) -> tuple[bytes, bytes]:
    """Return (ciphertext, nonce). Each call uses a fresh random nonce."""
    nonce = os.urandom(12)
    ciphertext = AESGCM(_master_key()).encrypt(nonce, plaintext.encode(), None)
    return ciphertext, nonce


def decrypt(ciphertext: bytes, nonce: bytes) -> str:
    return AESGCM(_master_key()).decrypt(nonce, ciphertext, None).decode()


async def set_secret(session: AsyncSession, user_id: uuid.UUID, kind: str, value: str) -> None:
    """Upsert one encrypted secret slot for a user. Empty value deletes the slot."""
    if kind not in SECRET_KINDS:
        raise ValueError(f"unknown secret kind {kind!r}")
    row = await session.get(UserSecret, (user_id, kind))
    if not value:  # blank clears the slot
        if row is not None:
            await session.delete(row)
        return
    ciphertext, nonce = encrypt(value)
    if row is None:
        session.add(UserSecret(user_id=user_id, kind=kind, ciphertext=ciphertext, nonce=nonce))
    else:
        row.ciphertext = ciphertext
        row.nonce = nonce
    await session.flush()


async def get_secret(session: AsyncSession, user_id: uuid.UUID, kind: str) -> str | None:
    """Decrypt one secret slot, or None if unset. Server-side use only."""
    row = await session.get(UserSecret, (user_id, kind))
    return decrypt(row.ciphertext, row.nonce) if row is not None else None


async def secret_status(session: AsyncSession, user_id: uuid.UUID) -> dict[str, bool]:
    """Which slots are set, as booleans (never plaintext) — safe to send to the client."""
    res = await session.execute(select(UserSecret.kind).where(UserSecret.user_id == user_id))
    have = set(res.scalars().all())
    return {kind: kind in have for kind in SECRET_KINDS}
