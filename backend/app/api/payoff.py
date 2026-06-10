"""Analyse Payoff REST endpoint.

Reprices a basket across a spot grid for two curves — at-expiry (intrinsic) and a
projected "what-if" at a target time-to-expiry + IV shift (Black-Scholes, r=0) —
plus net strategy greeks, breakevens, and max P/L. One call per slider settle (no
per-pixel round-trips). No live data needed; the client passes each leg's IV/entry.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.engines.payoff import (
    ScenarioLeg,
    _find_breakevens,
    distinct_expiries,
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
    t_years: float = 0.0  # this leg's time-to-expiry from now (years); enables calendars


class PayoffRequest(BaseModel):
    legs: list[PayoffLegIn]
    spot: float  # reference spot (greeks + marker)
    lo: float  # x-axis low spot
    hi: float  # x-axis high spot
    points: int = 81
    elapsed_years: float = 0.0  # time from now to the target scenario date (0 = now)
    iv_shift: float = 0.0  # added to every leg's IV (absolute vol, 0.05 = +5 pts)


@router.post("")
async def payoff(req: PayoffRequest) -> dict[str, Any]:
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
            t_years=max(leg.t_years, 0.0),
        )
        for leg in req.legs
    ]
    points = max(11, min(req.points, 401))
    prices = np.linspace(req.lo, req.hi, points)

    # One at-expiry curve per distinct leg expiry (nearest first). At each expiry, legs
    # expiring then are intrinsic while later-dated legs keep their residual time value —
    # so a calendar shows a tent at the front expiry and the full intrinsic at the back.
    exps = distinct_expiries(legs) or [0.0]
    expiry_curves = [
        {"tYears": float(t), "pnl": [float(v) for v in projected_pnl(legs, prices, t, 0.0)]}
        for t in exps
    ]
    # The front (earliest) expiry is the primary curve: back-compat `expiry` + max/L/BEs.
    front = projected_pnl(legs, prices, exps[0], 0.0)

    elapsed = max(req.elapsed_years, 0.0)
    projected = projected_pnl(legs, prices, elapsed, req.iv_shift)
    g = net_greeks(legs, req.spot, elapsed, req.iv_shift)

    return {
        "spots": [float(s) for s in prices],
        "expiries": expiry_curves,  # [{tYears, pnl}], nearest first (one per expiry date)
        "expiry": [float(v) for v in front],  # primary (front) expiry curve
        "projected": [float(v) for v in projected],
        "greeks": g,
        "breakevens": _find_breakevens(prices, front),
        "max_profit": float(np.max(front)),
        "max_loss": float(np.min(front)),
    }
