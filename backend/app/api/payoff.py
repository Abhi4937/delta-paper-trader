"""Analyse Payoff REST endpoint.

Reprices a basket across a spot grid for two curves — at-expiry (intrinsic) and a
projected "what-if" at a target time-to-expiry + IV shift (Black-Scholes, r=0) —
plus net strategy greeks, breakevens, and max P/L. One call per slider settle (no
per-pixel round-trips). No live data needed; the client passes each leg's IV/entry.
"""

from __future__ import annotations

import numpy as np
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.engines.payoff import (
    ScenarioLeg,
    _find_breakevens,
    net_greeks,
    projected_pnl,
)

router = APIRouter(prefix="/api/payoff", tags=["payoff"])


class PayoffLegIn(BaseModel):
    option_type: str  # "call" | "put"
    side: str  # "buy" | "sell"
    qty: float
    strike: float
    entry: float  # entry premium (per unit underlying)
    iv: float  # per-leg IV (fraction, e.g. 0.45)
    contract_value: float


class PayoffRequest(BaseModel):
    legs: list[PayoffLegIn]
    spot: float  # reference spot (greeks + marker)
    lo: float  # x-axis low spot
    hi: float  # x-axis high spot
    points: int = 81
    t_years: float = 0.0  # projected time-to-expiry (0 = expiry)
    iv_shift: float = 0.0  # added to every leg's IV (absolute vol, 0.05 = +5 pts)


@router.post("")
async def payoff(req: PayoffRequest) -> dict:
    if not req.legs:
        raise HTTPException(400, "no legs")
    legs = [
        ScenarioLeg(
            option_type="call" if leg.option_type == "call" else "put",
            side="long" if leg.side == "buy" else "short",
            qty=leg.qty,
            strike=leg.strike,
            entry=leg.entry,
            iv=leg.iv,
            contract_value=leg.contract_value,
        )
        for leg in req.legs
    ]
    points = max(11, min(req.points, 401))
    prices = np.linspace(req.lo, req.hi, points)

    expiry = projected_pnl(legs, prices, 0.0, 0.0)  # t=0 → intrinsic = at-expiry
    projected = projected_pnl(legs, prices, max(req.t_years, 0.0), req.iv_shift)
    g = net_greeks(legs, req.spot, max(req.t_years, 0.0), req.iv_shift)

    return {
        "spots": [float(s) for s in prices],
        "expiry": [float(v) for v in expiry],
        "projected": [float(v) for v in projected],
        "greeks": g,
        "breakevens": _find_breakevens(prices, expiry),
        "max_profit": float(np.max(expiry)),
        "max_loss": float(np.min(expiry)),
    }
