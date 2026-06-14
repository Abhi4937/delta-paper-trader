# backend/tests/test_rollup_series_integration.py
# Requires the dev TimescaleDB (same one alembic targets). Skips if unreachable.
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.sim.service import _rollup_series

pytestmark = pytest.mark.asyncio

DB_URL = os.environ.get("DATABASE_URL", "postgresql+asyncpg://paper:paper@localhost:5432/paper_trader")


async def test_rollup_preserves_high_low_across_buckets() -> None:
    engine = create_async_engine(DB_URL)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    pid = uuid.uuid4()
    uid = uuid.uuid4()
    base = datetime(2026, 1, 1, tzinfo=UTC)
    try:
        async with Session() as s:
            # Satisfy FK chain: strategy_series.position_id -> positions.id -> users.id.
            await s.execute(
                text(
                    "INSERT INTO users (id, email, display_name) "
                    "VALUES (:u, :email, 'rollup-test')"
                ),
                {"u": uid, "email": f"rollup-{uid}@test.local"},
            )
            await s.execute(
                text(
                    "INSERT INTO positions "
                    "(id, user_id, name, underlying, expiry, margin, entry_margin, "
                    "margin_badge, opened_at, status, auto_exit, auto_exit_suspended) "
                    "VALUES (:p, :u, 'rollup', 'BTC', '2026-01-31', 0, 0, 'est', :t, "
                    "'open', false, false)"
                ),
                {"p": pid, "u": uid, "t": base},
            )
            # 6 rows @ 10s within one minute; row 4 spikes high, row 2 spikes low.
            # (Both extremes land in the same [base, base+1min) bucket so the single
            # rolled-up candle must carry the true high and low.)
            for i in range(6):
                hi = 100.0 if i == 4 else 10.0
                lo = -100.0 if i == 2 else 5.0
                await s.execute(
                    text(
                        "INSERT INTO strategy_series "
                        "(time, position_id, user_id, pnl, pnl_open, pnl_high, pnl_low, delta, theta, vega, atm_iv, legs) "
                        "VALUES (:t,:p,:u,:c,:o,:h,:l,0,0,0,'{}'::jsonb,'{}'::jsonb)"
                    ),
                    {"t": base + timedelta(seconds=10 * i), "p": pid, "u": uid,
                     "c": 9.0, "o": 8.0, "h": hi, "l": lo},
                )
            await s.commit()
            pts = await _rollup_series(s, pid, 60, base, base + timedelta(minutes=1))
            await s.execute(text("DELETE FROM strategy_series WHERE position_id = :p"), {"p": pid})
            await s.execute(text("DELETE FROM positions WHERE id = :p"), {"p": pid})
            await s.execute(text("DELETE FROM users WHERE id = :u"), {"u": uid})
            await s.commit()
    except Exception as exc:  # noqa: BLE001 - DB unreachable => skip, not fail
        await engine.dispose()
        pytest.skip(f"dev TimescaleDB unreachable: {exc}")
    await engine.dispose()
    assert len(pts) == 1
    assert pts[0]["pnlHigh"] == 100.0  # spike preserved
    assert pts[0]["pnlLow"] == -100.0  # trough preserved
