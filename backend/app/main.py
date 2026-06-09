"""FastAPI application entrypoint.

Walking skeleton: a health endpoint + an app WebSocket that streams a heartbeat.
This proves the backend->browser realtime path before any Delta wiring lands.
Run: uv run uvicorn app.main:app --reload --port 8010
(Port 8010 by default to avoid colliding with other local services on 8000.)
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from typing import Any, cast

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api import chain as chain_api
from app.api import margin as margin_api
from app.api import payoff as payoff_api
from app.api import sim as sim_api
from app.config import get_settings
from app.db.session import SessionLocal
from app.delta.rest import DeltaRestClient
from app.services import chain as chain_svc
from app.services.margin import MarginService
from app.services.market_data import MarketDataIngestor
from app.sim.ticker import SimTicker
from app.sim.user import ensure_stub_user

settings = get_settings()


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Own the shared Delta client, httpx client, margin service, and WS ingestor."""
    app.state.delta = DeltaRestClient(settings)
    app.state.http = httpx.AsyncClient(timeout=20.0)
    app.state.margin = MarginService(settings)
    app.state.market = MarketDataIngestor(settings)
    await app.state.market.start()
    # seed the stub user/account, then start the server-authoritative sim tick loop
    async with SessionLocal() as session:
        await ensure_stub_user(session)
        await session.commit()
    app.state.sim_ticker = SimTicker(app)
    await app.state.sim_ticker.start()
    try:
        yield
    finally:
        await app.state.sim_ticker.stop()
        await app.state.market.stop()
        await app.state.delta.aclose()
        await app.state.http.aclose()


app = FastAPI(title="Paper Trader API", version=__version__, lifespan=lifespan)
app.include_router(chain_api.router)
app.include_router(margin_api.router)
app.include_router(payoff_api.router)
app.include_router(sim_api.router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok", "version": __version__, "env": settings.app_env}


@app.get("/api/feed")
async def feed() -> dict[str, Any]:
    """Delta market-data feed freshness — powers the stale-data guard (block order
    placement when prices may be frozen). `fresh` is False if the Delta WS is down
    or no message has arrived in >5s."""
    return cast("dict[str, Any]", app.state.market.feed_status())


@app.get("/api/marks")
async def marks(symbols: str) -> dict[str, Any]:
    """Latest mark/bid/ask for arbitrary symbols (any expiry) from the WS cache —
    powers live MTM for held positions regardless of the chain currently viewed."""
    cache = app.state.market.tickers
    out: dict[str, dict[str, Any]] = {}
    for s in (sym for sym in symbols.split(",") if sym):
        t = cache.get(s)
        if not t:
            continue
        q = t.get("quotes") or {}
        g = t.get("greeks") or {}
        out[s] = {
            "mark": float(t.get("mark_price") or 0),
            "bid": float(q.get("best_bid") or 0),
            "ask": float(q.get("best_ask") or 0),
            "iv": float(q.get("mark_iv") or 0),
            # signed per-option greeks (read-only, from the WS cache); the client
            # aggregates these into position greeks (signed_qty x cv x greek).
            "delta": float(g.get("delta") or 0),
            "gamma": float(g.get("gamma") or 0),
            "theta": float(g.get("theta") or 0),
            "vega": float(g.get("vega") or 0),
        }
    return out


@app.get("/api/atm-iv")
async def atm_iv(underlying: str, expiries: str) -> dict[str, float | None]:
    """ATM mark-IV per expiry for an underlying, computed from the WS cache.

    Powers the position-analytics IV panel for held strategies regardless of the
    chain currently viewed. Read-only — no order path. `expiries` is comma-sep ISO.
    """
    market = app.state.market
    out: dict[str, float | None] = {}
    for e in (x for x in expiries.split(",") if x):
        try:
            d = date.fromisoformat(e)
        except ValueError:
            continue
        out[e] = market.chain(underlying, d).atm_iv
    return out


@app.get("/api/orderbook")
async def orderbook(symbol: str) -> dict[str, Any]:
    """Live L2 order book (depth) for a contract — Delta /v2/l2orderbook."""
    data = await app.state.delta.get(f"/v2/l2orderbook/{symbol}")
    res = data.get("result", data) if isinstance(data, dict) else {}
    return {
        "symbol": symbol,
        "buy": res.get("buy", []),
        "sell": res.get("sell", []),
    }


@app.websocket("/ws")
async def ws(websocket: WebSocket) -> None:
    """App heartbeat WebSocket (liveness)."""
    await websocket.accept()
    try:
        n = 0
        while True:
            await websocket.send_json(
                {"type": "heartbeat", "seq": n, "ts": datetime.now(UTC).isoformat()}
            )
            n += 1
            await asyncio.sleep(1.0)
    except WebSocketDisconnect:
        return
    except asyncio.CancelledError:
        with contextlib.suppress(Exception):
            await websocket.close()
        raise


@app.websocket("/ws/chain")
async def ws_chain(websocket: WebSocket) -> None:
    """Stream the LIVE Delta option chain for an underlying+expiry.

    Client sends `{"underlying":"BTC","expiry":"2026-06-19"}` once; we push the
    real chain (+ all expiries) from the native Delta-WS ingestor cache, pushed
    every ~0.3s. Live push data — no REST polling.
    """
    await websocket.accept()
    market = app.state.market
    try:
        params = await websocket.receive_json()
        underlying = str(params.get("underlying", "BTC")).upper()
        expiry_str = params.get("expiry")
        while True:
            expiries = market.expiries(underlying)
            exp = (
                date.fromisoformat(expiry_str)
                if expiry_str
                else chain_svc.default_expiry(expiries)
            )
            chain = market.chain(underlying, exp)
            await websocket.send_json(
                {
                    "type": "chain",
                    "chain": chain.model_dump(mode="json"),
                    "expiries": [e.isoformat() for e in expiries],
                }
            )
            await asyncio.sleep(0.3)
    except WebSocketDisconnect:
        return
    except asyncio.CancelledError:
        with contextlib.suppress(Exception):
            await websocket.close()
        raise
