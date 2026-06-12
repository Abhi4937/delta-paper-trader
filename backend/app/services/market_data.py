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
import time
from datetime import date

import websockets

from app.config import Settings
from app.delta.rest import DeltaRestClient
from app.services.chain import OptionChain, build_chain, list_expiries, symbol_diff

log = logging.getLogger("market_data")
UNDERLYINGS = ("BTC", "ETH")
# How often to re-fetch the instrument universe so newly-listed expiries appear
# and settled ones drop out (the seed is otherwise frozen at WS-connect time).
RESEED_INTERVAL_S = 120


class MarketDataIngestor:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.tickers: dict[str, dict] = {}
        self._symbols: list[str] = []
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._task: asyncio.Task | None = None
        self.connected = False
        self.last_message_at: float | None = None  # time.monotonic() of last WS msg

    def feed_status(self) -> dict[str, object]:
        """Freshness of the Delta market-data feed (drives the stale-data guard).

        `fresh` is False whenever the Delta WS is disconnected OR no message has
        arrived in >5s — so the client never trades on frozen prices.
        """
        age = None if self.last_message_at is None else time.monotonic() - self.last_message_at
        return {
            "connected": self.connected,
            "age_seconds": age,
            "fresh": bool(self.connected and age is not None and age < 5.0),
        }

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

    async def _subscribe(self, symbols: list[str]) -> None:
        """Subscribe a set of symbols on the live socket (chunked at 100).

        v2/ticker = 5s snapshot (greeks/IV/OI); mark_price = ~1-2s live mark that
        Delta's own positions UI uses. Subscribe to both.
        """
        if self._ws is None:
            return
        for i in range(0, len(symbols), 100):
            chunk = symbols[i : i + 100]
            await self._ws.send(json.dumps({
                "type": "subscribe",
                "payload": {"channels": [
                    {"name": "v2/ticker", "symbols": chunk},
                    {"name": "mark_price", "symbols": ["MARK:" + s for s in chunk]},
                ]},
            }))

    async def _reseed(self) -> None:
        """Re-fetch the instrument universe; add newly-listed symbols (and subscribe
        them on the live socket), evict delisted ones. New expiries that Delta lists
        intraday (e.g. at 17:30 IST) become visible without an app restart."""
        fresh: dict[str, dict] = {}
        async with DeltaRestClient(self.settings) as c:
            for u in UNDERLYINGS:
                data = await c.get(
                    "/v2/tickers",
                    params={
                        "contract_types": "call_options,put_options",
                        "underlying_asset_symbols": u,
                    },
                )
                for t in data.get("result", []):
                    fresh[t["symbol"]] = t
        new, gone = symbol_diff(set(self.tickers), set(fresh))
        for s in new:
            self.tickers[s] = fresh[s]  # seed snapshot; live feed overwrites next tick
        for s in gone:
            self.tickers.pop(s, None)
        self._symbols = sorted(self.tickers)
        if new:
            await self._subscribe(sorted(new))
        if new or gone:
            log.info("reseed: +%d new symbols, -%d delisted", len(new), len(gone))

    async def _reseed_loop(self) -> None:
        while True:
            await asyncio.sleep(RESEED_INTERVAL_S)
            try:
                await self._reseed()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.warning("reseed failed: %s", e)

    async def _run(self) -> None:
        while True:
            reseed_task: asyncio.Task | None = None
            try:
                await self._seed_symbols()
                async with websockets.connect(
                    f"{self.settings.delta_ws_base}", ping_interval=20, max_size=None
                ) as ws:
                    self._ws = ws
                    await self._subscribe(self._symbols)
                    self.connected = True
                    log.info("Delta WS connected, %d option symbols", len(self._symbols))
                    reseed_task = asyncio.create_task(self._reseed_loop())
                    async for raw in ws:
                        self.last_message_at = time.monotonic()  # feed-freshness clock
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
            finally:
                if reseed_task is not None:
                    reseed_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await reseed_task
                self._ws = None
                self.connected = False
