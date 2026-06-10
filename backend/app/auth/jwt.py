"""Verify Supabase access tokens (identity only — we never mint tokens).

Supabase signs tokens with asymmetric JWT signing keys (ES256/RS256); we verify them
against the project's public JWKS endpoint, selecting the key by the token's `kid`.
Legacy HS256 tokens (shared secret) are still accepted when SUPABASE_JWT_SECRET is set,
for the key-rotation window.
"""

from __future__ import annotations

from typing import Any

import jwt
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientError

from app.config import get_settings

_ASYMMETRIC = ["ES256", "RS256", "EdDSA"]
_jwks_client: PyJWKClient | None = None


class AuthError(Exception):
    """Raised when a token is missing, malformed, expired, or fails verification."""


def _jwks() -> PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        url = get_settings().jwks_url
        if not url:
            raise AuthError("auth is not configured (SUPABASE_URL/JWKS missing)")
        _jwks_client = PyJWKClient(url)
    return _jwks_client


def verify_supabase_token(token: str) -> dict[str, Any]:
    """Return verified claims, or raise AuthError. Requires `exp` and `sub`."""
    settings = get_settings()
    try:
        alg = jwt.get_unverified_header(token).get("alg")
    except jwt.PyJWTError as e:
        raise AuthError(str(e)) from e

    try:
        if alg == "HS256":
            if not settings.supabase_jwt_secret:
                raise AuthError("HS256 token but SUPABASE_JWT_SECRET is not configured")
            return jwt.decode(
                token, settings.supabase_jwt_secret, algorithms=["HS256"],
                audience=settings.supabase_jwt_aud, options={"require": ["exp", "sub"]},
            )
        key = _jwks().get_signing_key_from_jwt(token).key
        return jwt.decode(
            token, key, algorithms=_ASYMMETRIC,
            audience=settings.supabase_jwt_aud, options={"require": ["exp", "sub"]},
        )
    except jwt.PyJWTError as e:
        raise AuthError(str(e)) from e
    except PyJWKClientError as e:
        raise AuthError(f"jwks: {e}") from e


def is_mfa(claims: dict[str, Any]) -> bool:
    """True once the session has cleared TOTP 2FA (Supabase assurance level aal2)."""
    return claims.get("aal") == "aal2"
