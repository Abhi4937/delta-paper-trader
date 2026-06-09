"""Local portfolio-margin engine — replicates Delta's methodology (ADR 0002).

29-scenario stress test (price x vol shocks) for Risk Margin, plus the Margin
Floor, giving Initial and Maintenance margin. Constants are Delta's published
BTC values, held in `MarginParams` so calibration against the live estimator can
tune them. Black-Scholes priced locally (math.erf), r=0 for crypto.

This is an *estimate* until calibrated; the live estimator (ADR 0001) is the
source of truth when a session token is present.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

import numpy as np

from app.engines.payoff import Leg as PayoffLeg
from app.engines.payoff import payoff_at_expiry

OptionType = Literal["call", "put", "future"]
Side = Literal["long", "short"]


# --------------------------------------------------------------------------- #
# Black-Scholes (r = 0 default)
# --------------------------------------------------------------------------- #
def _ncdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_price(
    option_type: OptionType, S: float, K: float, T: float, sigma: float, r: float = 0.0
) -> float:
    """Black-Scholes price; intrinsic when T<=0 or sigma<=0."""
    if T <= 0 or sigma <= 0 or S <= 0:
        return max(S - K, 0.0) if option_type == "call" else max(K - S, 0.0)
    sqrt_t = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    disc = math.exp(-r * T)
    if option_type == "call":
        return S * _ncdf(d1) - K * disc * _ncdf(d2)
    return K * disc * _ncdf(-d2) - S * _ncdf(-d1)


def implied_vol(
    option_type: OptionType, S: float, K: float, T: float, price: float,
    r: float = 0.0, lo: float = 1e-3, hi: float = 5.0, iters: int = 64,
) -> float:
    """Invert Black-Scholes to the IV that reproduces `price` (bisection).

    Used so the local margin model anchors each option's value to its mark
    (removes the BS-vs-mark basis that throws off long-option margin).
    Returns 0.0 when there's no time value to imply.
    """
    if T <= 0 or price <= 0 or S <= 0:
        return 0.0
    intrinsic = max(S - K, 0.0) if option_type == "call" else max(K - S, 0.0)
    if price <= intrinsic:
        return lo
    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        if bs_price(option_type, S, K, T, mid, r) > price:
            hi = mid
        else:
            lo = mid
    return 0.5 * (lo + hi)


# --------------------------------------------------------------------------- #
# Parameters (Delta BTC published values — calibratable)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class MarginParams:
    # price shock span: clip(base + slope*(N-knee), base, cap)
    price_base: float = 0.01
    price_slope: float = 4e-8
    price_knee: float = 100_000
    price_cap: float = 0.10
    # vol down / up spans
    voldn_base: float = 0.06
    voldn_slope: float = 1.2e-7
    voldn_cap: float = 0.30
    volup_base: float = 0.09
    volup_slope: float = 1.8e-7
    volup_cap: float = 0.45
    vol_knee: float = 100_000
    # options/futures margin floor %
    om_base: float = 0.005
    om_slope: float = 5e-9
    om_knee: float = 200_000
    om_cap: float = 0.02  # BTC (ETH 0.05, other 0.10)
    short_floor_pct: float = 0.05
    # IV DTE adjustment + scenarios
    iv_dte_ref: float = 30.0
    iv_dte_exp: float = 0.30
    dte_floor_days: float = 1.0 / 24.0
    extreme_mult: float = 3.0
    extreme_weight: float = 1.0 / 3.0
    # maintenance
    mm_factor: float = 0.80
    sigma_floor: float = 1e-4
    risk_free: float = 0.0


_DEFAULT = MarginParams()


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def price_shock_span(notional: float, p: MarginParams) -> float:
    return _clip(p.price_base + p.price_slope * (notional - p.price_knee),
                 p.price_base, p.price_cap)


def vol_down_span(notional: float, p: MarginParams) -> float:
    return _clip(p.voldn_base + p.voldn_slope * (notional - p.vol_knee),
                 p.voldn_base, p.voldn_cap)


def vol_up_span(notional: float, p: MarginParams) -> float:
    return _clip(p.volup_base + p.volup_slope * (notional - p.vol_knee),
                 p.volup_base, p.volup_cap)


def om_pct(notional: float, p: MarginParams) -> float:
    return _clip(p.om_base + p.om_slope * (notional - p.om_knee), p.om_base, p.om_cap)


def iv_shock(span: float, dte_days: float, p: MarginParams | None = None) -> float:
    """DTE-adjusted IV shock: span * (30/DTE)^0.30."""
    p = p or _DEFAULT
    dte = max(dte_days, p.dte_floor_days)
    return span * (p.iv_dte_ref / dte) ** p.iv_dte_exp


# --------------------------------------------------------------------------- #
# Scenarios
# --------------------------------------------------------------------------- #
VolState = Literal["base", "up", "down"]


@dataclass(frozen=True)
class Scenario:
    price_mult: float  # fractional shift applied to spot: S' = S*(1+price_mult)
    vol_state: VolState
    weight: float  # 1.0 standard, 1/3 extreme


_PRICE_LEVELS = (0.0, 0.33, -0.33, 0.50, -0.50, 0.67, -0.67, 1.0, -1.0)
_VOL_STATES: tuple[VolState, ...] = ("base", "up", "down")


def scenarios(price_span: float, p: MarginParams | None = None) -> list[Scenario]:
    """29 scenarios: 9 price x 3 vol (27) + 2 extreme (weight 1/3)."""
    p = p or _DEFAULT
    out = [
        Scenario(level * price_span, vs, 1.0)
        for level in _PRICE_LEVELS
        for vs in _VOL_STATES
    ]
    out.append(Scenario(p.extreme_mult * price_span, "up", p.extreme_weight))
    out.append(Scenario(-p.extreme_mult * price_span, "up", p.extreme_weight))
    return out


# --------------------------------------------------------------------------- #
# Legs + result
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class MarginLeg:
    option_type: OptionType
    side: Side
    qty: float  # positive magnitude (lots/contracts)
    strike: float
    dte_days: float
    iv: float
    contract_value: float
    mark: float

    @property
    def signed_qty(self) -> float:
        return self.qty if self.side == "long" else -self.qty

    @property
    def t_years(self) -> float:
        return self.dte_days / 365.0


@dataclass(frozen=True)
class MarginResult:
    risk_margin: float
    margin_floor: float
    initial_margin: float
    maintenance_margin: float
    notional: float
    price_span: float = field(default=0.0)


def _leg_value(leg: MarginLeg, spot: float, sigma: float, p: MarginParams) -> float:
    if leg.option_type == "future":
        return spot
    return bs_price(leg.option_type, spot, leg.strike, leg.t_years, sigma, p.risk_free)


def _risk_margin(legs: list[MarginLeg], spot: float, p: MarginParams,
                 price_span: float, vd: float, vu: float) -> float:
    bases = [_leg_value(leg, spot, leg.iv, p) for leg in legs]
    worst = 0.0
    for sc in scenarios(price_span, p):
        s_shock = spot * (1.0 + sc.price_mult)
        pnl = 0.0
        for leg, base in zip(legs, bases, strict=True):
            if leg.option_type == "future":
                v = s_shock
            else:
                if sc.vol_state == "up":
                    sigma = leg.iv + iv_shock(vu, leg.dte_days, p)
                elif sc.vol_state == "down":
                    sigma = max(leg.iv - iv_shock(vd, leg.dte_days, p), p.sigma_floor)
                else:
                    sigma = leg.iv
                v = _leg_value(leg, s_shock, sigma, p)
            pnl += leg.signed_qty * leg.contract_value * (v - base)
        loss = -pnl * sc.weight
        worst = max(worst, loss)
    return worst


def _margin_floor(legs: list[MarginLeg], spot: float, p: MarginParams,
                  om: float) -> float:
    floor = 0.0
    long_fut_notional = 0.0
    short_fut_notional = 0.0
    for leg in legs:
        notional = leg.qty * leg.contract_value * spot
        if leg.option_type == "future":
            if leg.side == "long":
                long_fut_notional += notional
            else:
                short_fut_notional += notional
            continue
        premium = leg.qty * leg.contract_value * leg.mark
        base = max(p.short_floor_pct * premium, om * notional)
        if leg.side == "short":
            floor += base
        else:
            floor += min(premium, base)
    floor += om * max(long_fut_notional, short_fut_notional)
    return floor


def defined_max_loss(legs: list[MarginLeg], spot: float) -> float:
    """Worst-case loss at expiry over a wide price grid (incl. every strike).

    For a defined-risk structure (condor, debit spread, long option) this is
    finite and small; for naked shorts it is huge over the grid, so capping the
    margin at it is a no-op there. Assumes a single contract_value (BTC options).
    """
    if not legs:
        return 0.0
    cv = legs[0].contract_value
    plegs = [
        PayoffLeg(kind=leg.option_type, side=leg.side, strike=leg.strike,
                  entry_price=leg.mark, qty=leg.qty)
        for leg in legs
    ]
    strikes = [leg.strike for leg in legs if leg.option_type != "future"]
    grid = np.unique(np.concatenate([
        np.linspace(1.0, spot * 5.0, 4001),
        np.array([*strikes, spot], dtype=float),
    ]))
    pnl = payoff_at_expiry(plegs, grid, contract_value=cv)
    return max(0.0, -float(np.min(pnl)))


def compute_margin(
    legs: list[MarginLeg], spot: float, params: MarginParams | None = None,
    ucf: float = 0.0,
) -> MarginResult:
    """Initial + maintenance portfolio margin for a basket (ADR 0002)."""
    p = params or MarginParams()
    notional = sum(leg.qty * leg.contract_value * spot for leg in legs)
    pspan = price_shock_span(notional, p)
    vd = vol_down_span(notional, p)
    vu = vol_up_span(notional, p)
    om = om_pct(notional, p)

    risk = _risk_margin(legs, spot, p, pspan, vd, vu)
    floor = _margin_floor(legs, spot, p, om)
    # A long option is funded by the premium it pays (max loss = premium). Delta's
    # portfolio_margin for a long == its premium (confirmed vs live: long call
    # margin 2.178 == cash outflow 2.163 == qty*cv*mark). So margin is at least
    # the long premium, then risk/floor on top of the short/net exposure.
    # A long option is funded by its premium (margin == premium paid; confirmed
    # vs live). Risk/floor cover the short/net exposure.
    long_premium = sum(
        leg.qty * leg.contract_value * leg.mark
        for leg in legs
        if leg.option_type != "future" and leg.side == "long"
    )
    # NOTE: for net-credit defined-risk spreads (condor, double calendar) Delta
    # applies `- UCF` where UCF = net credit (margin = gross - credit, derived from
    # the calibration captures). We don't subtract it yet — the gross Floor differs
    # from ours and the fit is noisy across strikes — so those exotics OVER-estimate
    # (~1.3-1.6x), which is the conservative/safe direction for a fallback. The live
    # endpoint is exact; continuous calibration tightens this. (ADR 0002.)
    im = max(max(risk, floor), long_premium) - ucf
    mm = p.mm_factor * (im + ucf) - ucf
    return MarginResult(
        risk_margin=risk,
        margin_floor=floor,
        initial_margin=im,
        maintenance_margin=mm,
        notional=notional,
        price_span=pspan,
    )
