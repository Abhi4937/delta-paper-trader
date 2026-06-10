"""MarginService: exact live margin (primary) with the local model as fallback.

Primary  = Delta's `estimate_margin/basket` via the web-session token (exact).
Fallback = the local portfolio-margin engine (ADR 0002) when no/expired token.
Returns a MarginQuote tagged `matched` (live) / `est` (local) / `stale`, and logs
the live-vs-local divergence to drive continuous calibration.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Literal, cast

import httpx
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db.models import MarginCalibration
from app.engines.margin import MarginLeg, compute_margin, implied_vol
from app.services.chain import Contract

_CAL_ALPHA = 0.15  # EWMA weight on each new (exact/local) observation

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
    calibration_factor: float | None = None  # learned local→exact correction in effect


def _order(leg: BasketLeg) -> dict[str, Any]:
    return {
        "product_id": leg.contract.product_id,
        "side": leg.side,
        "size": leg.size,
        "order_type": "market_order",
        "time_in_force": "gtc",
    }


def _margin_leg(leg: BasketLeg, spot: float, today: date) -> MarginLeg:
    c = leg.contract
    ot = cast(Literal["call", "put", "future"], c.option_type)
    dte = max((c.expiry - today).days, 1) if c.expiry else 1
    iv = implied_vol(ot, spot, c.strike, dte / 365.0, c.mark_price or 0.0)
    if iv <= 0:
        iv = c.quote.mark_iv or 0.0
    return MarginLeg(
        option_type=ot,
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
        # underlying -> (factor, samples); learned (exact/local) correction, EWMA-updated.
        self._cal: dict[str, tuple[float, int]] = {}

    def _factor(self, underlying: str) -> float:
        return self._cal.get(underlying.upper(), (1.0, 0))[0]

    def _observe(self, underlying: str, exact: float, local: float) -> None:
        """Update the EWMA correction from a fresh (exact, local) pair."""
        if local <= 0 or exact <= 0:
            return
        ratio = exact / local
        u = underlying.upper()
        factor, n = self._cal.get(u, (1.0, 0))
        factor = ratio if n == 0 else _CAL_ALPHA * ratio + (1 - _CAL_ALPHA) * factor
        self._cal[u] = (factor, n + 1)

    async def load_calibration(self, session: AsyncSession) -> None:
        res = await session.execute(select(MarginCalibration))
        self._cal = {c.underlying: (c.factor, c.samples) for c in res.scalars().all()}

    async def persist_calibration(self, session: AsyncSession) -> None:
        for u, (factor, n) in self._cal.items():
            row = await session.get(MarginCalibration, u)
            if row is None:
                session.add(MarginCalibration(underlying=u, factor=factor, samples=n))
            else:
                row.factor, row.samples = factor, n

    async def _live(
        self, client: httpx.AsyncClient, underlying: str, legs: list[BasketLeg], token: str | None
    ) -> float | None:
        """Exact margin via the web estimator with the given token; None if no token/error."""
        if not token:
            return None
        url = f"{self.s.delta_web_base}/v2/orders/estimate_margin/basket"
        payload = {
            "index_symbol": INDEX_SYMBOL.get(underlying.upper(), ".DEXBTUSD"),
            "orders": [_order(leg) for leg in legs],
            "source": "desktop",
        }
        headers = {
            "authorization": token,  # raw token, no "Bearer"
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
        web_jwt: str | None = None,
    ) -> MarginQuote:
        """Live primary -> local fallback. Token precedence: the GLOBAL web token first,
        then the user's vault token (`web_jwt`) if the global is absent or rejected."""
        local = self._local(underlying, legs, spot)
        live = await self._live(client, underlying, legs, self.s.delta_web_jwt)
        if live is None and web_jwt:
            live = await self._live(client, underlying, legs, web_jwt)
        if live is not None:
            self._observe(underlying, live, local)  # self-tune the fallback correction
            divergence = (local - live) / live * 100.0 if live else None
            return MarginQuote(
                margin=live, currency="USD", source="live", badge="matched",
                local_margin=local, divergence_pct=divergence,
                calibration_factor=self._factor(underlying),
            )
        # A token was tried but rejected -> "stale"; no token at all -> "est". Apply the
        # learned correction so the fallback tracks Delta even without a token.
        badge = "stale" if (self.s.delta_web_jwt or web_jwt) else "est"
        factor = self._factor(underlying)
        return MarginQuote(
            margin=local * factor, currency="USD", source="local", badge=badge,
            local_margin=local, calibration_factor=factor,
        )
