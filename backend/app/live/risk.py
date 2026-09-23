"""Pure risk math for live Delta positions — no I/O, fully unit-tested.

- where each leg's native Delta stop sits (derived from its USD stop / the basket floor)
- the widening IOC price band used to close without "skipping" the exit
- margin-usage alert levels
- SL-vs-liquidation: does the stop fire before Delta's liquidation (equity <= MM)?
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Literal

from app.engines.margin import MarginLeg, bs_price, compute_margin, implied_vol

Side = Literal["buy", "sell"]

# Close-price bands for successive IOC attempts; past the last one we send a market order.
CLOSE_BANDS: tuple[float, ...] = (0.05, 0.10, 0.20, 0.40)
LIQ_BUFFER = 1.25  # equity at the SL must be >= 1.25 x maintenance margin to be "green"
SLIPPAGE_ALLOWANCE = 0.10  # assume the close fills 10% of premium worse than the mark
USAGE_LEVELS: tuple[tuple[float, str], ...] = (
    (0.90, "emergency"),
    (0.80, "critical"),
    (0.60, "warning"),
)


def effective_stop_pnl(leg_stop: float | None, basket_floor: float | None) -> float | None:
    """USD loss (negative) at which one leg must be cut. A leg's own stop, else the basket
    floor — so with our server down no single leg can lose more than the basket limit.
    With both, the tighter (closer to zero) wins."""
    stops = [-abs(s) for s in (leg_stop, basket_floor) if s is not None]
    return max(stops) if stops else None


def native_stop_price(
    side: Side, entry: float, qty: float, cv: float, stop_pnl: float | None, tick: float
) -> float | None:
    """Premium at which the leg's P&L reaches `stop_pnl`. A short is cut when the premium
    RISES; a long when it FALLS. None when no stop is possible (a long whose max loss —
    its premium — is already inside the stop)."""
    if stop_pnl is None or qty <= 0 or cv <= 0:
        return None
    per_unit = abs(stop_pnl) / (qty * cv)
    if side == "sell":
        return _round_tick(entry + per_unit, tick, up=True)
    price = entry - per_unit
    return _round_tick(price, tick, up=False) if price > tick else None


def close_side(side: Side) -> Side:
    return "buy" if side == "sell" else "sell"


def ioc_limit_price(side_to_send: Side, mark: float, band: float, tick: float) -> float:
    """Worst price we accept on this attempt: buy up to mark*(1+band), sell down to
    mark*(1-band) (never below one tick)."""
    if side_to_send == "buy":  # a 0 mark would price the buy-back at 0 and never fill
        return _round_tick(max(mark, tick) * (1 + band), tick, up=True)
    return max(_round_tick(mark * (1 - band), tick, up=False), tick)


def margin_level(usage: float | None) -> str | None:
    if usage is None:
        return None
    for threshold, name in USAGE_LEVELS:
        if usage >= threshold:
            return name
    return None


def _round_tick(price: float, tick: float, *, up: bool) -> float:
    if tick <= 0:
        return price
    n = price / tick
    n = math.ceil(n - 1e-9) if up else math.floor(n + 1e-9)
    return round(n * tick, 10)


# --------------------------------------------------------------------------- #
# SL vs liquidation
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class RiskLeg:
    type: Literal["call", "put"]
    side: Side
    qty: float
    strike: float
    dte_days: float
    cv: float
    entry: float
    mark: float
    stop_pnl: float | None = None  # this leg's own USD stop (negative)
    in_group: bool = True  # False = another live position on the same account

    @property
    def sign(self) -> int:
        return 1 if self.side == "buy" else -1


@dataclass(frozen=True)
class LiqCheck:
    verdict: Literal["green", "yellow", "red"]
    sl_spot: float | None  # nearest spot (either direction) where the SL fires
    liq_spot: float | None  # nearest spot where equity <= maintenance margin
    equity: float
    maintenance_margin: float
    usage: float | None  # MM / equity now
    max_scale: float  # largest size multiplier of the group that stays green
    reason: str


def _iv(lg: RiskLeg, spot: float) -> float:
    iv = implied_vol(lg.type, spot, lg.strike, max(lg.dte_days, 1 / 24) / 365.0, lg.mark)
    return iv if iv > 0 else 0.5


def _mm(legs: list[RiskLeg], ivs: list[float], spot: float, prices: list[float]) -> float:
    if not legs:
        return 0.0
    mlegs = [
        MarginLeg(
            option_type=lg.type,
            side="long" if lg.side == "buy" else "short",
            qty=lg.qty,
            strike=lg.strike,
            dte_days=max(lg.dte_days, 1 / 24),
            iv=iv,
            contract_value=lg.cv,
            mark=px,
        )
        for lg, iv, px in zip(legs, ivs, prices, strict=True)
    ]
    return compute_margin(mlegs, spot).maintenance_margin


def check_sl_vs_liquidation(
    legs: list[RiskLeg],
    *,
    spot: float,
    balance: float,
    basket_floor: float | None,
) -> LiqCheck:
    """Scan spot outward (both directions, IV held at today's level) and compare where the
    group's SL fires with where the account hits liquidation (equity <= MM, Delta's rule).

    equity = wallet balance + net option value at marks (shorts negative). Green when, at
    the SL spot, equity after slippage is >= LIQ_BUFFER x MM there.
    ponytail: IV held constant across the scan and legs on other underlyings ignored;
    add an IV-up shock if live calibration shows MM at the SL running above this estimate.
    """
    if not legs or spot <= 0:
        return LiqCheck("yellow", None, None, balance, 0.0, None, 0.0, "no legs / no spot")
    res = _scan(legs, spot, balance, basket_floor)
    lo, hi = 0.0, 20.0
    if res.verdict == "green":
        for _ in range(12):  # binary search the largest green size multiplier
            mid = (lo + hi) / 2
            scaled = [replace(lg, qty=lg.qty * mid) if lg.in_group else lg for lg in legs]
            # trading the extra size at the mark moves cash and position value equally,
            # so equity today is unchanged: offset the balance by the added value
            added = sum(
                lg.sign * lg.qty * (mid - 1) * lg.cv * lg.mark for lg in legs if lg.in_group
            )
            ok = _scan(scaled, spot, balance - added, basket_floor).verdict == "green"
            lo, hi = (mid, hi) if ok else (lo, mid)
    return replace(res, max_scale=round(lo, 2))


def _scan(
    legs: list[RiskLeg],
    spot: float,
    balance: float,
    basket_floor: float | None,
) -> LiqCheck:
    ivs = [_iv(lg, spot) for lg in legs]
    marks = [lg.mark for lg in legs]
    equity = balance + sum(lg.sign * lg.qty * lg.cv * lg.mark for lg in legs)
    mm_now = _mm(legs, ivs, spot, marks)
    usage = mm_now / equity if equity > 0 else None
    group = [i for i, lg in enumerate(legs) if lg.in_group]
    # stops are fixed USD amounts: a bigger what-if size reaches them on a smaller move
    floor = -abs(basket_floor) if basket_floor is not None else None

    worst: LiqCheck | None = None
    for direction in (1, -1):
        sl_spot = liq_spot = None
        sl_eq = sl_mm = 0.0
        for step in range(1, 201):  # 0.25% steps out to +-50%
            s = spot * (1 + direction * 0.0025 * step)
            t_prices = [
                bs_price(lg.type, s, lg.strike, max(lg.dte_days, 1 / 24) / 365.0, iv)
                for lg, iv in zip(legs, ivs, strict=True)
            ]
            dval = sum(
                lg.sign * lg.qty * lg.cv * (p - lg.mark)
                for lg, p in zip(legs, t_prices, strict=True)
            )
            eq = equity + dval
            mm = _mm(legs, ivs, s, t_prices)
            if liq_spot is None and eq <= mm:
                liq_spot = s
            if sl_spot is None:
                leg_pnls = {
                    i: legs[i].sign * legs[i].qty * legs[i].cv * (t_prices[i] - legs[i].entry)
                    for i in group
                }
                leg_hit = any(
                    (stop := legs[i].stop_pnl) is not None and leg_pnls[i] <= -abs(stop)
                    for i in group
                )
                basket_hit = floor is not None and sum(leg_pnls.values()) <= floor
                if leg_hit or basket_hit:
                    sl_spot = s
                    slip = SLIPPAGE_ALLOWANCE * sum(
                        legs[i].qty * legs[i].cv * t_prices[i] for i in group
                    )
                    sl_eq, sl_mm = eq - slip, mm
            if liq_spot is not None:
                break
        way = "up" if direction == 1 else "down"
        if sl_spot is None:
            if liq_spot is not None:
                r = LiqCheck(
                    "red",
                    None,
                    liq_spot,
                    equity,
                    mm_now,
                    usage,
                    0.0,
                    f"liquidation before any SL on a move {way}",
                )
            else:
                r = LiqCheck(
                    "green",
                    None,
                    None,
                    equity,
                    mm_now,
                    usage,
                    0.0,
                    f"no SL and no liquidation within 50% {way}",
                )
        elif liq_spot is not None and _nearer(liq_spot, sl_spot, spot):
            r = LiqCheck(
                "red",
                sl_spot,
                liq_spot,
                equity,
                mm_now,
                usage,
                0.0,
                f"liquidation comes before the SL on a move {way}",
            )
        elif sl_eq < LIQ_BUFFER * sl_mm:
            r = LiqCheck(
                "yellow",
                sl_spot,
                liq_spot,
                equity,
                mm_now,
                usage,
                0.0,
                f"SL fires {way} with equity only {sl_eq / sl_mm:.2f}x maintenance margin",
            )
        else:
            r = LiqCheck(
                "green",
                sl_spot,
                liq_spot,
                equity,
                mm_now,
                usage,
                0.0,
                f"SL fires {way} with equity {sl_eq / sl_mm:.2f}x maintenance margin"
                if sl_mm > 0
                else f"SL fires {way}",
            )
        worst = r if worst is None or _rank(r.verdict) > _rank(worst.verdict) else worst
    assert worst is not None
    return worst


def _nearer(a: float, b: float, spot: float) -> bool:
    """a is reached no later than b on the way out from spot (same direction)."""
    return abs(a - spot) <= abs(b - spot)


def _rank(v: str) -> int:
    return {"green": 0, "yellow": 1, "red": 2}[v]
