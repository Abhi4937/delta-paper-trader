"""Stub single-user resolution + account seeding.

The schema is already user-isolated (`user_id` everywhere); until real auth lands
we resolve one trusted stub user. Seeds the User + Account + opening deposit on
first run (mirrors the client's seeded $5,000 paper account).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import Account, LedgerEntry, Log, User

_cached_user_id: uuid.UUID | None = None


async def ensure_stub_user(session: AsyncSession) -> uuid.UUID:
    """Return the stub user's id, creating the user/account/opening-deposit if absent."""
    global _cached_user_id
    if _cached_user_id is not None:
        return _cached_user_id

    settings = get_settings()
    email = settings.stub_user_email
    res = await session.execute(select(User).where(User.email == email))
    user = res.scalar_one_or_none()
    if user is None:
        start = float(settings.virtual_start_balance_usd)
        user = User(email=email, display_name="Paper Trader")
        session.add(user)
        await session.flush()  # assign user.id
        now = datetime.now(UTC)
        session.add(
            Account(
                user_id=user.id, balance_usd=start, start_balance_usd=start, currency="USD"
            )
        )
        session.add(
            LedgerEntry(
                user_id=user.id, t=now, type="deposit", amount=start, balance_after=start,
                ref="seed",
            )
        )
        session.add(
            Log(
                user_id=user.id, t=now, action="session",
                detail=f"Paper account funded ${start:,.0f}", tone="info",
            )
        )
        await session.flush()

    _cached_user_id = user.id
    return user.id
