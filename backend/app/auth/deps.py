"""FastAPI auth dependencies.

`current_user` replaces the old `ensure_stub_user` call at every endpoint: it verifies
the Supabase token, enforces mandatory 2FA + the invite allowlist, provisions the local
user on first login, and returns the local `user_id`. `require_admin` adds the admin gate.

Dev bypass: when `app_env == "dev"` and a request arrives WITHOUT a bearer token, we fall
back to the single stub user so local development keeps working without Supabase.
"""

from __future__ import annotations

import uuid

from fastapi import Depends, HTTPException, Request, WebSocket
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import AuthError, is_mfa, verify_supabase_token
from app.config import get_settings
from app.db.models import AllowedEmail, User
from app.db.session import get_session
from app.sim.user import ensure_stub_user, get_or_create_user_by_supabase


async def _user_from_token(session: AsyncSession, token: str | None) -> User:
    """Shared resolution for HTTP + WS. Returns the local User or raises HTTPException."""
    settings = get_settings()

    if not token:
        if settings.is_dev:
            uid = await ensure_stub_user(session)
            user = await session.get(User, uid)
            assert user is not None
            return user
        raise HTTPException(401, "missing bearer token")

    try:
        claims = verify_supabase_token(token)
    except AuthError as e:
        raise HTTPException(401, f"invalid token: {e}") from e

    if not is_mfa(claims):
        # Authenticated but no 2FA yet — force enrollment before any access.
        raise HTTPException(403, "2FA enrollment required")

    sub = claims.get("sub")
    email = (claims.get("email") or "").strip().lower()
    if not sub or not email:
        raise HTTPException(401, "token missing sub/email")

    is_admin_seed = bool(settings.admin_email) and email == settings.admin_email.strip().lower()
    allowed = await session.get(AllowedEmail, email)
    if allowed is None and not is_admin_seed:
        raise HTTPException(403, "email not on the allowlist")

    user = await get_or_create_user_by_supabase(
        session, supabase_uid=sub, email=email, is_admin=is_admin_seed
    )
    if not user.is_active:
        raise HTTPException(403, "account disabled")
    return user


def _bearer(request: Request) -> str | None:
    header = request.headers.get("authorization")
    if header and header.lower().startswith("bearer "):
        return header[7:]
    return None


async def current_user(
    request: Request, session: AsyncSession = Depends(get_session)
) -> uuid.UUID:
    user = await _user_from_token(session, _bearer(request))
    return user.id


async def require_admin(
    request: Request, session: AsyncSession = Depends(get_session)
) -> uuid.UUID:
    user = await _user_from_token(session, _bearer(request))
    if not user.is_admin:
        raise HTTPException(403, "admin only")
    return user.id


async def resolve_ws_user(websocket: WebSocket, session: AsyncSession) -> uuid.UUID:
    """WebSocket auth: browsers can't set headers on WS, so the token rides as ?token=.

    Raises HTTPException (caller closes the socket on failure).
    """
    token = websocket.query_params.get("token")
    user = await _user_from_token(session, token)
    return user.id
