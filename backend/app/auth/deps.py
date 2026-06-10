"""FastAPI auth dependencies.

`current_user` replaces the old `ensure_stub_user` call at every endpoint: it verifies
the Supabase token, enforces the invite allowlist, provisions the local user on first
login, and returns the local `user_id`. `require_admin` adds the admin gate; `require_mfa`
demands a 2FA-cleared session (for future live/real-money endpoints — 2FA is optional for
paper trading).

Dev bypass: when `app_env == "dev"` and a request arrives WITHOUT a bearer token, we fall
back to the single stub user so local development keeps working without Supabase.
"""

from __future__ import annotations

import uuid

from fastapi import Depends, HTTPException, Request
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
        if settings.is_dev and settings.allow_dev_no_auth:
            uid = await ensure_stub_user(session)
            user = await session.get(User, uid)
            assert user is not None
            return user
        raise HTTPException(401, "missing bearer token")

    try:
        claims = verify_supabase_token(token)
    except AuthError as e:
        raise HTTPException(401, f"invalid token: {e}") from e

    # 2FA is OPTIONAL for paper trading (no real money). Live/real-money endpoints
    # (sub-project B/C) must use `require_mfa` to demand a 2FA-cleared (aal2) session.
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


async def require_mfa(
    request: Request, session: AsyncSession = Depends(get_session)
) -> uuid.UUID:
    """Gate for live/real-money actions: require a 2FA-cleared (aal2) session. 2FA is
    optional for paper trading, so wire this onto live endpoints (sub-project B/C) and
    the live-key save path when they land."""
    token = _bearer(request)
    if not token:
        raise HTTPException(401, "missing bearer token")
    try:
        claims = verify_supabase_token(token)
    except AuthError as e:
        raise HTTPException(401, f"invalid token: {e}") from e
    if not is_mfa(claims):
        raise HTTPException(403, "two-factor authentication required for this action")
    user = await _user_from_token(session, token)
    return user.id


async def resolve_ws_user(token: str | None, session: AsyncSession) -> uuid.UUID:
    """WebSocket auth. The token is sent in the Sec-WebSocket-Protocol handshake header
    (not the URL — keeps it out of access logs); the handler extracts it and passes it
    here. Raises HTTPException (caller closes the socket on failure)."""
    user = await _user_from_token(session, token)
    return user.id
