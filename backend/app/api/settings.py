"""Per-user settings: the Delta credential vault.

GET returns only which slots are SET (booleans) — plaintext is NEVER returned.
PUT accepts plaintext, encrypts it via the vault. A field left null is unchanged;
an empty string clears that slot.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import vault
from app.auth.deps import current_user
from app.db.session import get_session

router = APIRouter(prefix="/api/settings", tags=["settings"])


class SecretsIn(BaseModel):
    """All optional. null = leave unchanged; "" = clear; value = set."""

    delta_read_key: str | None = None
    delta_read_secret: str | None = None
    delta_trade_key: str | None = None
    delta_trade_secret: str | None = None
    delta_web_jwt: str | None = None


@router.get("/keys")
async def get_keys(
    session: AsyncSession = Depends(get_session),
    user_id: uuid.UUID = Depends(current_user),
) -> dict[str, bool]:
    return await vault.secret_status(session, user_id)


@router.put("/keys")
async def put_keys(
    body: SecretsIn,
    session: AsyncSession = Depends(get_session),
    user_id: uuid.UUID = Depends(current_user),
) -> dict[str, bool]:
    for kind, value in body.model_dump().items():
        if value is not None:  # null => leave unchanged
            await vault.set_secret(session, user_id, kind, value)
    return await vault.secret_status(session, user_id)
