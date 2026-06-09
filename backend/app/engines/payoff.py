"""Strategy payoff at expiry: P/L curve, breakevens, max profit/loss.

Pure functions over a price grid — no live data needed. Used by the Strategy
Builder's payoff chart. Currency/units are caller-defined via `contract_value`
(the per-contract multiplier); compute in quote ccy then convert to INR upstream.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import numpy as np
from numpy.typing import NDArray

from app.engines.blackscholes import bs_price as _bs_price
from app.engines.blackscholes import greeks as _greeks

LegKind = Literal["call", "put", "future"]
LegSide = Literal["long", "short"]


@dataclass(frozen=True)
class Leg:
    """One leg of a strategy.

    For options, `strike` is used; for futures, `strike` is ignored and the leg
    is linear in the underlying around `entry_price`.
    """

    kind: LegKind
    side: LegSide
    strike: float
    entry_price: float
    qty: float = 1.0

    @property
    def sign(self) -> int:
        return 1 if self.side == "long" else -1


@dataclass(frozen=True)
class PayoffProfile:
    prices: NDArray[np.float64]
    pnl: NDArray[np.float64]
    breakevens: list[float] = field(default_factory=list)
    max_profit: float = 0.0
    max_loss: float = 0.0
    spot: float = 0.0


def _intrinsic(leg: Leg, prices: NDArray[np.float64]) -> NDArray[np.float64]:
    if leg.kind == "call":
        return np.maximum(prices - leg.strike, 0.0)
    if leg.kind == "put":
        return np.maximum(leg.strike - prices, 0.0)
    # future: value relative to entry handled in payoff_at_expiry
    return prices


def payoff_at_expiry(
    legs: list[Leg],
    prices: NDArray[np.float64],
    contract_value: float = 1.0,
) -> NDArray[np.float64]:
    """Total P/L of `legs` at expiry across `prices`.

    Options:  sign * (intrinsic - entry_price) * qty * cv
    Futures:  sign * (price    - entry_price) * qty * cv
    """
    prices = np.asarray(prices, dtype=np.float64)
    total = np.zeros_like(prices)
    for leg in legs:
        if leg.kind == "future":
            leg_pnl = leg.sign * (prices - leg.entry_price)
        else:
            leg_pnl = leg.sign * (_intrinsic(leg, prices) - leg.entry_price)
        total += leg_pnl * leg.qty * contract_value
    return total


def _find_breakevens(
    prices: NDArray[np.float64], pnl: NDArray[np.float64]
) -> list[float]:
    """Zero-crossings of the P/L curve via linear interpolation."""
    bes: list[float] = []
    sign = np.sign(pnl)
    for i in range(len(pnl) - 1):
        a, b = pnl[i], pnl[i + 1]
        if a == 0.0:
            bes.append(float(prices[i]))
        elif sign[i] != sign[i + 1] and sign[i + 1] != 0:
            # interpolate the crossing between prices[i] and prices[i+1]
            t = a / (a - b)
            bes.append(float(prices[i] + t * (prices[i + 1] - prices[i])))
    # dedupe near-equal crossings
    out: list[float] = []
    for be in bes:
        if not any(abs(be - x) < 1e-6 for x in out):
            out.append(be)
    return out


def payoff_profile(
    legs: list[Leg],
    spot: float,
    lo: float,
    hi: float,
    points: int = 2001,
    contract_value: float = 1.0,
) -> PayoffProfile:
    """Build the full payoff profile over [lo, hi]."""
    prices = np.linspace(lo, hi, points)
    pnl = payoff_at_expiry(legs, prices, contract_value)
    return PayoffProfile(
        prices=prices,
        pnl=pnl,
        breakevens=_find_breakevens(prices, pnl),
        max_profit=float(np.max(pnl)),
        max_loss=float(np.min(pnl)),
        spot=spot,
    )


# --------------------------------------------------------------------------- #
# Analyse Payoff: projected (theoretical) P/L + net greeks at a what-if scenario
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ScenarioLeg:
    """An option leg with the data needed to reprice it (Black-Scholes, r=0)."""

    option_type: LegKind  # "call" | "put"
    side: LegSide
    qty: float
    strike: float
    entry: float  # entry premium (per unit underlying)
    iv: float  # current per-leg IV (fraction)
    contract_value: float

    @property
    def signed_qty(self) -> float:
        return self.qty if self.side == "long" else -self.qty


def projected_pnl(
    legs: list[ScenarioLeg], prices: NDArray[np.float64], t_years: float, iv_shift: float
) -> NDArray[np.float64]:
    """Theoretical net P/L across `prices` at `t_years` to expiry with IV shifted by
    `iv_shift`. At t_years=0 this equals the at-expiry payoff (intrinsic)."""
    prices = np.asarray(prices, dtype=np.float64)
    total = np.zeros_like(prices)
    for leg in legs:
        sigma = max(leg.iv + iv_shift, 0.0)
        vals = np.array(
            [_bs_price(leg.option_type, float(s), leg.strike, t_years, sigma) for s in prices]
        )
        total += leg.signed_qty * (vals - leg.entry) * leg.contract_value
    return total


def net_greeks(
    legs: list[ScenarioLeg], spot: float, t_years: float, iv_shift: float
) -> dict[str, float]:
    """Net strategy greeks at `spot` for the scenario: Σ signed_qty × cv × per-option greek."""
    out = {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0}
    for leg in legs:
        sigma = max(leg.iv + iv_shift, 0.0)
        g = _greeks(leg.option_type, spot, leg.strike, t_years, sigma)
        k = leg.signed_qty * leg.contract_value
        for key in out:
            out[key] += k * g[key]
    return out
