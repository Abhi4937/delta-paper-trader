"""MarginService: exact live margin (primary) with the local model as fallback.

Primary  = Delta's `estimate_margin/basket` via the web-session token (exact).
Fallback = the local portfolio-margin engine (ADR 0002) when no/expired token.
Returns a MarginQuote tagged `matched` (live) / `est` (local) / `stale`, and logs
the live-vs-local divergence to drive continuous calibration.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import httpx
from pydantic import BaseModel

from app.config import Settings, get_settings
from app.engines.margin import MarginLeg, compute_margin, implied_vol
from app.services.chain import Contract

# Underlying -> Delta spot-index symbol used by the estimator basket.
INDEX_SYMBOL = {"BTC": ".DEXBTUSD", "ETH": ".DEXETHUSD"}


class BasketLeg(BaseModel):
    """One leg of a margin request, resolved to a live Contract."""

    contract: Contract
    side: str  # "buy" | "sell"
    size: int

    model_config = {"arbitrary_types_allowed": True}


@dataclass(frozen=True)
class MarginQuote:
    margin: float
    currency: str
    source: str  # "live" | "local"
    badge: str  # "matched" | "est" | "stale"
    maintenance_margin: float | None = None
    local_margin: float | None = None
    divergence_pct: float | None = None


def _order(leg: BasketLeg) -> dict:
    return {
        "product_id": leg.contract.product_id,
        "side": leg.side,
        "size": leg.size,
        "order_type": "market_order",
        "time_in_force": "gtc",
    }


def _margin_leg(leg: BasketLeg, spot: float, today: date) -> MarginLeg:
    c = leg.contract
    dte = max((c.expiry - today).days, 1) if c.expiry else 1
    iv = implied_vol(c.option_type, spot, c.strike, dte / 365.0, c.mark_price or 0.0)
    if iv <= 0:
        iv = c.quote.mark_iv or 0.0
    return MarginLeg(
        option_type=c.option_type,
        side="long" if leg.side == "buy" else "short",
        qty=leg.size,
        strike=c.strike,
        dte_days=dte,
        iv=iv,
        contract_value=c.contract_value or 0.001,
        mark=c.mark_price or 0.0,
    )


class MarginService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.s = settings or get_settings()

    async def _live(
        self, client: httpx.AsyncClient, underlying: str, legs: list[BasketLeg]
    ) -> float | None:
        """Exact margin via the web estimator; None if no/expired token or error."""
        if not self.s.delta_web_jwt:
            return None
        url = f"{self.s.delta_web_base}/v2/orders/estimate_margin/basket"
        payload = {
            "index_symbol": INDEX_SYMBOL.get(underlying.upper(), ".DEXBTUSD"),
            "orders": [_order(leg) for leg in legs],
            "source": "desktop",
        }
        headers = {
            "authorization": self.s.delta_web_jwt,  # raw token, no "Bearer"
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0",
        }
        try:
            r = await client.post(url, json=payload, headers=headers)
        except httpx.HTTPError:
            return None
        if r.status_code == 401:
            return None
        if r.status_code != 200:
            return None
        try:
            return float(r.json()["result"]["portfolio_margin"])
        except (KeyError, ValueError, TypeError):
            return None

    def _local(self, underlying: str, legs: list[BasketLeg], spot: float) -> float:
        today = date.today()
        mlegs = [_margin_leg(leg, spot, today) for leg in legs]
        return compute_margin(mlegs, spot).initial_margin

    async def get_margin(
        self,
        client: httpx.AsyncClient,
        underlying: str,
        legs: list[BasketLeg],
        spot: float,
        token_present: bool | None = None,
    ) -> MarginQuote:
        """Live primary -> local fallback, with divergence logged in the quote."""
        local = self._local(underlying, legs, spot)
        live = await self._live(client, underlying, legs)
        if live is not None:
            divergence = (local - live) / live * 100.0 if live else None
            return MarginQuote(
                margin=live, currency="USD", source="live", badge="matched",
                local_margin=local, divergence_pct=divergence,
            )
        # No/expired token -> local estimate. Badge "stale" if a token was set but
        # rejected, else "est".
        badge = "stale" if self.s.delta_web_jwt else "est"
        return MarginQuote(
            margin=local, currency="USD", source="local", badge=badge,
            local_margin=local,
        )
