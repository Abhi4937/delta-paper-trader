"""Read-only adapter over the live MarketDataIngestor for the sim engine.

Pulls the latest quote (mark/bid/ask/iv/greeks) per symbol and spot/ATM-IV per
underlying from the in-memory WS cache. No order path — read-only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.services.market_data import MarketDataIngestor


@dataclass(frozen=True)
class Quote:
    mark: float
    bid: float
    ask: float
    iv: float
    delta: float
    gamma: float
    theta: float
    vega: float


class MarketView:
    def __init__(self, market: MarketDataIngestor) -> None:
        self.m = market

    def quote(self, symbol: str) -> Quote | None:
        t = self.m.tickers.get(symbol)
        if not t:
            return None
        q = t.get("quotes") or {}
        g = t.get("greeks") or {}
        return Quote(
            mark=float(t.get("mark_price") or 0),
            bid=float(q.get("best_bid") or 0),
            ask=float(q.get("best_ask") or 0),
            iv=float(q.get("mark_iv") or 0),
            delta=float(g.get("delta") or 0),
            gamma=float(g.get("gamma") or 0),
            theta=float(g.get("theta") or 0),
            vega=float(g.get("vega") or 0),
        )

    def spot(self, underlying: str) -> float:
        for t in self.m.tickers.values():
            if t.get("underlying_asset_symbol") == underlying:
                sp = t.get("spot_price")
                if sp:
                    return float(sp)
        return 0.0

    def atm_iv(self, underlying: str, expiry: str) -> float:
        try:
            d = date.fromisoformat(expiry)
        except ValueError:
            return 0.0
        return self.m.chain(underlying, d).atm_iv or 0.0

    def fresh(self) -> bool:
        """Whole-feed freshness (drives auto-exit suspension — never act on bad data)."""
        return bool(self.m.feed_status().get("fresh"))
