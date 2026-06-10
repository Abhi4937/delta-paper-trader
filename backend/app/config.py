"""Application settings, loaded from environment / .env.

Secrets (Delta keys, DB creds) live ONLY here, server-side. Never expose to the client.
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Delta Exchange India (READ-ONLY key; no orders are ever placed)
    delta_api_base: str = "https://api.india.delta.exchange"
    delta_ws_base: str = "wss://socket.india.delta.exchange"
    delta_api_key: str = ""
    delta_api_secret: str = ""

    # Margin estimator: the /v2/orders/estimate_margin/basket endpoint does NOT
    # accept API keys — it needs the web-session bearer JWT. Used READ-ONLY for
    # margin estimation; the web client never places orders.
    delta_web_base: str = "https://cdn.india.deltaex.org"
    delta_web_jwt: str = ""

    # Infra
    database_url: str = "postgresql+asyncpg://paper:paper@localhost:5432/paper_trader"
    redis_url: str = "redis://localhost:6379/0"

    # Auth (Supabase = identity only: login + TOTP 2FA + JWTs). The backend verifies
    # Supabase access tokens locally with the project JWT secret; app data stays in our
    # own Timescale Postgres. See docs ADR for the topology.
    supabase_url: str = ""  # e.g. https://<ref>.supabase.co
    # Tokens are verified against the project's public JWKS (asymmetric ES256/RS256).
    # Defaults to {supabase_url}/auth/v1/.well-known/jwks.json when blank.
    supabase_jwks_url: str = ""
    # Optional: only for legacy HS256 tokens during a signing-key rotation window.
    supabase_jwt_secret: str = ""
    supabase_jwt_aud: str = "authenticated"

    @property
    def jwks_url(self) -> str:
        if self.supabase_jwks_url:
            return self.supabase_jwks_url
        if self.supabase_url:
            return f"{self.supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"
        return ""
    # Per-user secret vault: master key (urlsafe base64, 32 bytes) for AES-GCM at rest.
    secret_vault_key: str = ""
    # First admin + first allowlist entry are seeded from this email on startup.
    admin_email: str = ""

    # App
    app_env: str = "dev"
    cors_origins: str = "http://localhost:3000"
    virtual_start_balance_inr: int = Field(default=500_000)
    # Sim: single trusted user used as the dev bypass when app_env == "dev" and a request
    # arrives without a bearer token (schema is already user-isolated).
    stub_user_email: str = "trader@paper.local"
    virtual_start_balance_usd: float = 5000.0  # matches client START_BALANCE

    @property
    def is_dev(self) -> bool:
        return self.app_env == "dev"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
