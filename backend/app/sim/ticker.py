"""Per-user simulation tick loop (server-authoritative).

Every ~1s: recompute MTM from live marks, run auto-exit (24/7, suspended on stale
feed), append a per-second sample to the in-memory series ring (keeps the 1s
charts), write a ~10s row to the Timescale hypertable (durable, full life of the
position), and periodically refresh exact margin. Runs for ALL users' open positions.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.auth.vault import get_secret
from app.db.models import Position, StrategySeries
from app.db.session import SessionLocal
from app.sim import service
from app.sim.margin_helper import quote_margin
from app.sim.marketview import MarketView

log = logging.getLogger("sim_ticker")

SERIES_CAP = 24 * 60 * 60  # 24h of 1s samples per position (in-memory ring)
DB_WRITE_EVERY = 10.0  # 10s durable downsample → Timescale hypertable (full life of the position)
MARGIN_REFRESH_EVERY = 30.0


class SimTicker:
    def __init__(self, app: Any) -> None:
        self.app = app
        self._task: asyncio.Task[None] | None = None
        self.series: dict[str, list[dict[str, Any]]] = {}
        app.state.sim_series = self.series  # read by get_state / WS
        self._last_db = 0.0
        self._last_margin = 0.0

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _run(self) -> None:
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.warning("sim tick error: %s", e)
            await asyncio.sleep(1.0)

    async def _tick(self) -> None:
        mv = MarketView(self.app.state.market)
        mono = time.monotonic()
        write_db = (mono - self._last_db) >= DB_WRITE_EVERY
        refresh_margin = (mono - self._last_margin) >= MARGIN_REFRESH_EVERY

        async with SessionLocal() as session:
            # ALL users' open positions (not just one) — every user's MTM history,
            # auto-exit, and margin refresh run server-side.
            res = await session.execute(
                select(Position)
                .where(Position.status == "open")
                .options(selectinload(Position.legs))
            )
            positions = list(res.scalars().all())
            now = datetime.now(UTC)

            for p in positions:
                uid = p.user_id
                decision = service.evaluate_position_exit(p, mv)
                p.auto_exit_suspended = decision.kind == "suspended"
                if decision.kind == "close-strategy":
                    await service.close_position(
                        session, uid, self.app.state, p.id, decision.reason
                    )
                elif decision.kind == "close-legs":
                    for lid in decision.leg_ids:
                        await service.close_leg(
                            session, uid, self.app.state, p.id, uuid.UUID(lid), decision.reason
                        )

                if p.status != "open":
                    continue

                sample = service.build_sample(p, mv, now)  # full → durable 10s DB row
                ring = self.series.setdefault(str(p.id), [])
                # Lightweight ring: net P&L + greeks only (per-leg/IV detail stays in the
                # 10s DB). ~5x less RAM so a 24h 1-second window fits a small backend.
                ring.append(
                    {
                        "t": sample["t"],
                        "pnl": sample["pnl"],
                        "delta": sample["delta"],
                        "theta": sample["theta"],
                        "vega": sample["vega"],
                        "atmIv": {},
                        "legs": {},
                    }
                )
                if len(ring) > SERIES_CAP:
                    del ring[: len(ring) - SERIES_CAP]
                if write_db:
                    session.add(
                        StrategySeries(
                            time=now,
                            position_id=p.id,
                            user_id=uid,
                            pnl=sample["pnl"],
                            delta=sample["delta"],
                            theta=sample["theta"],
                            vega=sample["vega"],
                            atm_iv=sample["atmIv"],
                            legs=sample["legs"],
                        )
                    )

                if refresh_margin:
                    open_legs = [
                        (lg.product_id, lg.side, lg.qty) for lg in p.legs if lg.status == "open"
                    ]
                    if open_legs:
                        with contextlib.suppress(Exception):
                            web_jwt = await get_secret(session, uid, "delta_web_jwt")
                            mq = await quote_margin(
                                self.app.state, p.underlying, open_legs, web_jwt=web_jwt
                            )
                            p.margin, p.margin_badge = mq.margin, mq.badge

            if write_db:
                self._last_db = mono
            if refresh_margin:
                self._last_margin = mono
            await session.commit()
