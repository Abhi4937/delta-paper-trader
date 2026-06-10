"""Verify Supabase access tokens (HS256, signed with the project JWT secret).

Identity only: we never mint tokens — Supabase does. We validate signature, expiry and
audience, and surface the claims (sub, email, aal) to the auth dependency.
"""

from __future__ import annotations

from typing import Any

import jwt

from app.config import get_settings


class AuthError(Exception):
    """Raised when a token is missing, malformed, expired, or fails verification."""


def verify_supabase_token(token: str) -> dict[str, Any]:
    """Return verified claims, or raise AuthError. Requires `exp` and `sub` present."""
    settings = get_settings()
    if not settings.supabase_jwt_secret:
        raise AuthError("auth is not configured (SUPABASE_JWT_SECRET missing)")
    try:
        return jwt.decode(
            token,
            settings.supabase_jwt_secret,
            algorithms=["HS256"],
            audience=settings.supabase_jwt_aud,
            options={"require": ["exp", "sub"]},
        )
    except jwt.PyJWTError as e:
        raise AuthError(str(e)) from e


def is_mfa(claims: dict[str, Any]) -> bool:
    """True once the session has cleared TOTP 2FA (Supabase assurance level aal2)."""
    return claims.get("aal") == "aal2"
