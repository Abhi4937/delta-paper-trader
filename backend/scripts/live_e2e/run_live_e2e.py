"""Run the real backend against the FAKE Delta exchange (fake_delta.py on :8099).

- dev auth bypass on, LIVE_TRADING_ENABLED on — but every LiveClient is forced to the
  fake exchange, so no request can reach real Delta.
- market data still comes from real Delta (real marks for the fake positions).
- stores a fake trade key for the local dev stub user, and on exit (Ctrl+C) deletes it
  plus every live group / LIVE log row it created, so your normal backend never tries the
  fake key against real Delta.

Needs the dev DB (docker start paper_trader_db) and nothing else on :8010.
Run from backend/:  uv run python scripts/live_e2e/run_live_e2e.py
If it was killed hard:  uv run python scripts/live_e2e/run_live_e2e.py --cleanup
"""

import asyncio
import os
import sys

os.environ.update(APP_ENV="dev", ALLOW_DEV_NO_AUTH="true", LIVE_TRADING_ENABLED="true")

import uvicorn  # noqa: E402
from sqlalchemy import delete, select  # noqa: E402

import app.live.client as lc  # noqa: E402
from app.auth.vault import get_secret, set_secret  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db.models import Log, Position, User  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.sim.user import ensure_stub_user  # noqa: E402

FAKE = "http://127.0.0.1:8099"
FAKE_KEY = "fake-key"
_orig = lc.LiveClient.__init__


def _init(self, key, secret, settings=None, transport=None):
    s = (settings or get_settings()).model_copy(update={"delta_api_base": FAKE})
    _orig(self, key, secret, s, transport)


lc.LiveClient.__init__ = _init


async def seed_keys() -> None:
    async with SessionLocal() as s:
        uid = await ensure_stub_user(s)
        existing = await get_secret(s, uid, "delta_trade_key")
        if existing and existing != FAKE_KEY:
            raise SystemExit(
                "dev stub user already has a real-looking trade key; refusing to overwrite"
            )
        await set_secret(s, uid, "delta_trade_key", FAKE_KEY)
        await set_secret(s, uid, "delta_trade_secret", "fake-secret")
        await s.commit()
        print("stub user", uid, "seeded with the fake trade key", flush=True)
    await engine.dispose()  # pool connections belong to this loop; uvicorn starts a new one


async def cleanup() -> None:
    async with SessionLocal() as s:
        uid = (
            await s.execute(select(User.id).where(User.email == get_settings().stub_user_email))
        ).scalar_one()
        await set_secret(s, uid, "delta_trade_key", "")
        await set_secret(s, uid, "delta_trade_secret", "")
        n = (
            await s.execute(
                delete(Position).where(Position.user_id == uid, Position.source == "live")
            )
        ).rowcount
        await s.execute(delete(Log).where(Log.user_id == uid, Log.action.like("LIVE%")))
        await s.commit()
        print(f"cleaned up: fake key removed, {n} test live group(s) deleted", flush=True)
    await engine.dispose()


if __name__ == "__main__":
    if "--cleanup" in sys.argv:  # if the runner was killed before it could clean up
        asyncio.run(cleanup())
        sys.exit(0)
    asyncio.run(seed_keys())
    from app.main import app

    try:
        uvicorn.run(app, host="127.0.0.1", port=8010, log_level="warning")
    finally:
        asyncio.run(cleanup())
