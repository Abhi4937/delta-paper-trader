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
from collections import deque
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

# In-memory 1s ring per position: a COUNT-bounded deque (RAM stays flat, no time-based
# growth). 15 min of recent 1s detail is the chart tail served by GET /api/state; older
# history comes from the durable 10s DB rows. ~1 MB/position, stable (was unbounded 24h).
RING_MAXLEN = 15 * 60  # 900 samples = 15 min @ 1s
DB_WRITE_EVERY = 10.0  # 10s durable downsample → Timescale hypertable (full life of the position)
MARGIN_REFRESH_EVERY = 30.0
SETTLE_EVERY = 300.0  # settle expired legs at Delta's settlement price every 5 min (and on boot)


class SimTicker:
    def __init__(self, app: Any) -> None:
        self.app = app
        self._task: asyncio.Task[None] | None = None
        self.series: dict[str, deque[dict[str, Any]]] = {}
        app.state.sim_series = self.series  # read by get_state / WS
        self._last_db = 0.0
        self._last_margin = 0.0
        self._last_settle = 0.0

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

        # Periodically settle any legs past expiry at Delta's settlement price (own session).
        if (mono - self._last_settle) >= SETTLE_EVERY:
            self._last_settle = mono
            async with SessionLocal() as ssn:
                try:
                    n = await service.settle_expired_legs(ssn, self.app.state)
                    if n:
                        await ssn.commit()
                        log.info("settled %d expired leg(s) at Delta settlement price", n)
                except Exception as e:  # noqa: BLE001
                    log.warning("settlement error: %s", e)

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

                sample = service.build_sample(p, mv, now)
                # deque(maxlen) auto-evicts the oldest — O(1), constant RAM, no manual trim
                ring = self.series.setdefault(str(p.id), deque(maxlen=RING_MAXLEN))
                ring.append(sample)  # full sample — per-leg + IV detail kept for the charts
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

            # free rings of positions that are no longer open (avoid unbounded RAM growth)
            live_ids = {str(p.id) for p in positions if p.status == "open"}
            for dead in [k for k in self.series if k not in live_ids]:
                del self.series[dead]

            if write_db:
                self._last_db = mono
                with contextlib.suppress(Exception):
                    await self.app.state.margin.persist_calibration(session)
            if refresh_margin:
                self._last_margin = mono
            await session.commit()
