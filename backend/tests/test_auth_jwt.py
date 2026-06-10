"""Unit tests for Supabase token verification."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.config import get_settings

SECRET = "test-jwt-secret-do-not-use-in-prod"


@pytest.fixture(autouse=True)
def _configure(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    monkeypatch.setenv("SUPABASE_JWT_AUD", "authenticated")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _token(secret: str = SECRET, *, aud: str = "authenticated", aal: str = "aal2",
           exp_delta: timedelta = timedelta(hours=1), sub: str | None = "uid-123",
           email: str | None = "alice@example.com") -> str:
    claims: dict = {"aud": aud, "aal": aal, "exp": datetime.now(UTC) + exp_delta}
    if sub is not None:
        claims["sub"] = sub
    if email is not None:
        claims["email"] = email
    return jwt.encode(claims, secret, algorithm="HS256")


def test_valid_token_decodes() -> None:
    from app.auth.jwt import is_mfa, verify_supabase_token

    claims = verify_supabase_token(_token())
    assert claims["sub"] == "uid-123"
    assert claims["email"] == "alice@example.com"
    assert is_mfa(claims) is True


def test_aal1_is_not_mfa() -> None:
    from app.auth.jwt import is_mfa, verify_supabase_token

    claims = verify_supabase_token(_token(aal="aal1"))
    assert is_mfa(claims) is False


def test_expired_token_rejected() -> None:
    from app.auth.jwt import AuthError, verify_supabase_token

    with pytest.raises(AuthError):
        verify_supabase_token(_token(exp_delta=timedelta(hours=-1)))


def test_wrong_audience_rejected() -> None:
    from app.auth.jwt import AuthError, verify_supabase_token

    with pytest.raises(AuthError):
        verify_supabase_token(_token(aud="some-other-service"))


def test_forged_signature_rejected() -> None:
    from app.auth.jwt import AuthError, verify_supabase_token

    with pytest.raises(AuthError):
        verify_supabase_token(_token(secret="attacker-secret"))


def test_missing_sub_rejected() -> None:
    from app.auth.jwt import AuthError, verify_supabase_token

    with pytest.raises(AuthError):
        verify_supabase_token(_token(sub=None))


def test_es256_token_via_jwks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Asymmetric (current Supabase) tokens verify against the JWKS public key."""
    from cryptography.hazmat.primitives.asymmetric import ec

    from app.auth import jwt as ajwt

    priv = ec.generate_private_key(ec.SECP256R1())
    token = jwt.encode(
        {"aud": "authenticated", "aal": "aal2", "sub": "uid-9", "email": "e@x.com",
         "exp": datetime.now(UTC) + timedelta(hours=1)},
        priv, algorithm="ES256", headers={"kid": "k1"},
    )

    class _Key:
        key = priv.public_key()

    class _Client:
        def get_signing_key_from_jwt(self, _t: str) -> _Key:
            return _Key()

    monkeypatch.setattr(ajwt, "_jwks", lambda: _Client())
    claims = ajwt.verify_supabase_token(token)
    assert claims["sub"] == "uid-9" and ajwt.is_mfa(claims)
