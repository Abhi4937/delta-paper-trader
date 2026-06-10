"""User provisioning + account seeding.

The schema is user-isolated (`user_id` everywhere). This module owns creating a User
together with its paper Account + opening deposit. Two entry points:
- `ensure_stub_user` — the dev bypass (single trusted user when no bearer token).
- `get_or_create_user_by_supabase` — provisions/links a real user from a verified token.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import Account, LedgerEntry, Log, User

_cached_user_id: uuid.UUID | None = None


async def _seed_new_user(
    session: AsyncSession,
    *,
    email: str,
    display_name: str,
    supabase_uid: str | None = None,
    is_admin: bool = False,
) -> User:
    """Create a User + funded paper Account + opening-deposit ledger/log rows."""
    start = float(get_settings().virtual_start_balance_usd)
    user = User(
        email=email, display_name=display_name, supabase_uid=supabase_uid, is_admin=is_admin
    )
    session.add(user)
    await session.flush()  # assign user.id
    now = datetime.now(UTC)
    session.add(
        Account(user_id=user.id, balance_usd=start, start_balance_usd=start, currency="USD")
    )
    session.add(
        LedgerEntry(
            user_id=user.id, t=now, type="deposit", amount=start, balance_after=start, ref="seed"
        )
    )
    session.add(
        Log(
            user_id=user.id, t=now, action="session",
            detail=f"Paper account funded ${start:,.0f}", tone="info",
        )
    )
    await session.flush()
    return user


async def get_or_create_user_by_supabase(
    session: AsyncSession, *, supabase_uid: str, email: str, is_admin: bool = False
) -> User:
    """Resolve a real user from a verified token, provisioning on first login.

    If a row already exists for this email (e.g. a pre-seeded admin), link the
    Supabase uid to it rather than creating a duplicate.
    """
    res = await session.execute(select(User).where(User.supabase_uid == supabase_uid))
    user = res.scalar_one_or_none()
    if user is not None:
        if is_admin and not user.is_admin:
            user.is_admin = True
            await session.flush()
        return user

    res = await session.execute(select(User).where(User.email == email))
    user = res.scalar_one_or_none()
    if user is not None:
        user.supabase_uid = supabase_uid
        if is_admin:
            user.is_admin = True
        await session.flush()
        return user

    return await _seed_new_user(
        session,
        email=email,
        display_name=email.split("@")[0],
        supabase_uid=supabase_uid,
        is_admin=is_admin,
    )


async def ensure_stub_user(session: AsyncSession) -> uuid.UUID:
    """Return the dev stub user's id, creating user/account/opening-deposit if absent."""
    global _cached_user_id
    if _cached_user_id is not None:
        return _cached_user_id

    email = get_settings().stub_user_email
    res = await session.execute(select(User).where(User.email == email))
    user = res.scalar_one_or_none()
    if user is None:
        user = await _seed_new_user(session, email=email, display_name="Paper Trader")
    _cached_user_id = user.id
    return user.id
