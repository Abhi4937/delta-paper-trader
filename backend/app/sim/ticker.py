"""Per-user simulation tick loop (server-authoritative).

Every ~1s: recompute MTM from live marks, run auto-exit (24/7, suspended on stale
feed), append a per-second sample to the in-memory series ring (keeps the 1s
charts), write a 1-min row to the Timescale hypertable, and periodically refresh
exact margin. Single stub user for now; the schema is already user-isolated.
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

from app.db.models import Position, StrategySeries
from app.db.session import SessionLocal
from app.sim import service
from app.sim.margin_helper import quote_margin
from app.sim.marketview import MarketView
from app.sim.user import ensure_stub_user

log = logging.getLogger("sim_ticker")

SERIES_CAP = 12 * 60 * 60  # 12h of 1s samples per position (ring)
DB_WRITE_EVERY = 60.0  # 1-min durable downsample → hypertable
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
            user_id = await ensure_stub_user(session)
            res = await session.execute(
                select(Position)
                .where(Position.user_id == user_id, Position.status == "open")
                .options(selectinload(Position.legs))
            )
            positions = list(res.scalars().all())
            now = datetime.now(UTC)

            for p in positions:
                decision = service.evaluate_position_exit(p, mv)
                p.auto_exit_suspended = decision.kind == "suspended"
                if decision.kind == "close-strategy":
                    await service.close_position(session, user_id, self.app.state, p.id, decision.reason)
                elif decision.kind == "close-legs":
                    for lid in decision.leg_ids:
                        await service.close_leg(session, user_id, self.app.state, p.id, uuid.UUID(lid), decision.reason)

                if p.status != "open":
                    continue

                sample = service.build_sample(p, mv, now)
                ring = self.series.setdefault(str(p.id), [])
                ring.append(sample)
                if len(ring) > SERIES_CAP:
                    del ring[: len(ring) - SERIES_CAP]
                if write_db:
                    session.add(StrategySeries(
                        time=now, position_id=p.id, user_id=user_id, pnl=sample["pnl"],
                        delta=sample["delta"], theta=sample["theta"], vega=sample["vega"],
                        atm_iv=sample["atmIv"], legs=sample["legs"],
                    ))

                if refresh_margin:
                    open_legs = [(lg.product_id, lg.side, lg.qty) for lg in p.legs if lg.status == "open"]
                    if open_legs:
                        with contextlib.suppress(Exception):
                            mq = await quote_margin(self.app.state, p.underlying, open_legs)
                            p.margin, p.margin_badge = mq.margin, mq.badge

            if write_db:
                self._last_db = mono
            if refresh_margin:
                self._last_margin = mono
            await session.commit()
