"""Admin-only cross-user views + allowlist management.

Every route is gated by `require_admin` (verified token + `users.is_admin`). This is the
only place a user may read another user's data.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_admin
from app.db.models import AllowedEmail, Log, User
from app.db.session import get_session

router = APIRouter(prefix="/api/admin", tags=["admin"])


class AllowlistIn(BaseModel):
    email: str


@router.get("/users")
async def list_users(
    session: AsyncSession = Depends(get_session),
    _admin: uuid.UUID = Depends(require_admin),
) -> list[dict[str, Any]]:
    res = await session.execute(select(User).order_by(User.created_at))
    return [
        {
            "id": str(u.id),
            "email": u.email,
            "displayName": u.display_name,
            "isAdmin": u.is_admin,
            "isActive": u.is_active,
            "hasLogin": u.supabase_uid is not None,
            "createdAt": int(u.created_at.timestamp() * 1000) if u.created_at else None,
        }
        for u in res.scalars().all()
    ]


@router.get("/users/{user_id}/logs")
async def user_logs(
    user_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    _admin: uuid.UUID = Depends(require_admin),
) -> list[dict[str, Any]]:
    res = await session.execute(
        select(Log).where(Log.user_id == user_id).order_by(Log.t.desc()).limit(500)
    )
    return [
        {
            "t": int(log.t.timestamp() * 1000),
            "action": log.action,
            "detail": log.detail,
            "tone": log.tone,
        }
        for log in res.scalars().all()
    ]


@router.get("/allowlist")
async def list_allowlist(
    session: AsyncSession = Depends(get_session),
    _admin: uuid.UUID = Depends(require_admin),
) -> list[dict[str, Any]]:
    res = await session.execute(select(AllowedEmail).order_by(AllowedEmail.created_at))
    return [
        {
            "email": a.email,
            "invitedBy": a.invited_by,
            "createdAt": int(a.created_at.timestamp() * 1000) if a.created_at else None,
        }
        for a in res.scalars().all()
    ]


@router.post("/allowlist")
async def add_allowlist(
    body: AllowlistIn,
    session: AsyncSession = Depends(get_session),
    admin_id: uuid.UUID = Depends(require_admin),
) -> dict[str, str]:
    email = body.email.strip().lower()
    if await session.get(AllowedEmail, email) is None:
        admin = await session.get(User, admin_id)
        session.add(
            AllowedEmail(
                email=email,
                invited_by=admin.email if admin else None,
                created_at=datetime.now(UTC),
            )
        )
    return {"email": email}


@router.delete("/allowlist/{email}")
async def remove_allowlist(
    email: str,
    session: AsyncSession = Depends(get_session),
    _admin: uuid.UUID = Depends(require_admin),
) -> dict[str, str]:
    row = await session.get(AllowedEmail, email.strip().lower())
    if row is not None:
        await session.delete(row)
    return {"removed": email.strip().lower()}
