"""Native Delta WebSocket market-data ingestor.

Holds a persistent `v2/ticker` subscription for all BTC+ETH option symbols and
keeps an in-memory cache of the latest ticker per symbol (same shape as REST
`/v2/tickers`, so `build_chain` works directly). The app chain WS reads from this
cache — true push data, no REST polling. Auto-reconnects + resubscribes.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from datetime import date

import websockets

from app.config import Settings
from app.delta.rest import DeltaRestClient
from app.services.chain import OptionChain, build_chain, list_expiries

log = logging.getLogger("market_data")
UNDERLYINGS = ("BTC", "ETH")


class MarketDataIngestor:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.tickers: dict[str, dict] = {}
        self._symbols: list[str] = []
        self._task: asyncio.Task | None = None
        self.connected = False

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    # ---- reads -----------------------------------------------------------
    def _for(self, underlying: str) -> list[dict]:
        return [t for t in self.tickers.values() if t.get("underlying_asset_symbol") == underlying]

    def expiries(self, underlying: str) -> list[date]:
        return list_expiries(self._for(underlying))

    def chain(self, underlying: str, expiry: date | None) -> OptionChain:
        return build_chain(self._for(underlying), underlying, expiry)

    # ---- ws loop ---------------------------------------------------------
    async def _seed_symbols(self) -> None:
        """Seed the cache + symbol list from one REST snapshot per underlying."""
        async with DeltaRestClient(self.settings) as c:
            syms: list[str] = []
            for u in UNDERLYINGS:
                data = await c.get(
                    "/v2/tickers",
                    params={
                        "contract_types": "call_options,put_options",
                        "underlying_asset_symbols": u,
                    },
                )
                for t in data.get("result", []):
                    self.tickers[t["symbol"]] = t
                    syms.append(t["symbol"])
            self._symbols = syms

    async def _run(self) -> None:
        while True:
            try:
                await self._seed_symbols()
                async with websockets.connect(
                    f"{self.settings.delta_ws_base}", ping_interval=20, max_size=None
                ) as ws:
                    # v2/ticker = 5s snapshot (greeks/IV/OI); mark_price = ~1-2s live
                    # mark that Delta's own positions UI uses. Subscribe to both.
                    for i in range(0, len(self._symbols), 100):
                        chunk = self._symbols[i : i + 100]
                        await ws.send(json.dumps({
                            "type": "subscribe",
                            "payload": {"channels": [
                                {"name": "v2/ticker", "symbols": chunk},
                                {"name": "mark_price", "symbols": ["MARK:" + s for s in chunk]},
                            ]},
                        }))
                    self.connected = True
                    log.info("Delta WS connected, %d option symbols", len(self._symbols))
                    async for raw in ws:
                        m = json.loads(raw)
                        t = m.get("type")
                        if t == "v2/ticker" and "symbol" in m:
                            # keep the live mark if the snapshot's is older
                            sym = m["symbol"]
                            live = self.tickers.get(sym, {}).get("_live_mark")
                            self.tickers[sym] = m
                            if live is not None:
                                m["mark_price"] = live
                                m["_live_mark"] = live
                        elif t == "mark_price" and m.get("price") is not None:
                            sym = (m.get("symbol") or "").replace("MARK:", "")
                            tk = self.tickers.get(sym)
                            if tk is not None:
                                tk["mark_price"] = m["price"]
                                tk["_live_mark"] = m["price"]
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                self.connected = False
                log.warning("Delta WS error, reconnecting in 2s: %s", e)
                await asyncio.sleep(2)
