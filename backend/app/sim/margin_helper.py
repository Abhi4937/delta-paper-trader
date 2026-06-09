"""Reusable exact-margin quote for a set of legs (mirrors api/margin.py).

Resolves each (product_id, side, size) to a live Contract and runs MarginService
(Delta exact → local fallback). Used by the sim place/close-leg paths so margin is
computed server-side, the same way the builder's /api/margin does.
"""

from __future__ import annotations

from typing import Any

from app.services import chain as chain_svc
from app.services.margin import BasketLeg, MarginQuote, MarginService


async def quote_margin(
    app_state: Any, underlying: str, legs: list[tuple[int, str, float]]
) -> MarginQuote:
    """legs = [(product_id, side, size), ...]. Raises ValueError on unknown product."""
    delta = app_state.delta
    http = app_state.http
    service: MarginService = app_state.margin

    tickers = await chain_svc.fetch_option_tickers(delta, underlying.upper())
    by_id: dict[int, Any] = {}
    for t in tickers:
        c = chain_svc.normalize_contract(t)
        if c and c.product_id is not None:
            by_id[c.product_id] = c

    basket: list[BasketLeg] = []
    spot = 0.0
    for product_id, side, size in legs:
        c = by_id.get(product_id)
        if c is None:
            raise ValueError(f"unknown product_id {product_id}")
        spot = spot or (c.spot_price or 0.0)
        basket.append(BasketLeg(contract=c, side=side, size=int(size)))

    return await service.get_margin(http, underlying.upper(), basket, spot)
