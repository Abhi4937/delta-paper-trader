"""Paper-trading money model — ported verbatim from the validated client engine
(frontend/src/lib/store.ts + engine.ts). Same formulas → same numbers, so the
"validated vs a real Delta account @10 lots" result (see memory money-model-validated)
carries over. DO NOT change a formula here without re-validating on both sides.

Conventions: premium/price are Delta price-points; multiply by contract_value (BTC
per contract, e.g. 0.001) to get USD. 1 lot = 0.001 BTC. Settles in USD.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

Side = Literal["buy", "sell"]
OptType = Literal["call", "put"]


# --- Delta India options fee schedule --------------------------------------- #
# fee = min(notional_rate·notional, premium_cap·premium) · (1−discount) · (1+GST)
# per leg, charged on BOTH entry and exit. notional = spot·cv·qty, premium = price·cv·qty.
@dataclass(frozen=True)
class FeeSchedule:
    name: str
    notional_rate: float  # fraction of notional
    premium_cap: float  # max fraction of premium


FEE_SCHEDULES: dict[str, FeeSchedule] = {
    "standard": FeeSchedule("Standard", 0.0003, 0.10),  # 0.03% / 10%
    "options_carnival": FeeSchedule("Options Carnival", 0.0001, 0.035),  # 0.010% / 3.5%
}
# ACTIVE offer in force (verified to the cent vs Delta's calculator). Switch to
# "standard" when the Options Carnival promo ends.
ACTIVE_FEE: FeeSchedule = FEE_SCHEDULES["options_carnival"]
GST = 0.18  # 18% GST on the fee
FEE_DISCOUNT = 0.0  # DELTAEARN referral 10% — account not eligible


def leg_sign(side: Side) -> float:
    return 1.0 if side == "buy" else -1.0


def leg_fee(price: float, spot: float, cv: float, qty: float, fee: FeeSchedule = ACTIVE_FEE) -> float:
    base = min(fee.notional_rate * spot * cv * qty, fee.premium_cap * price * cv * qty)
    return base * (1.0 - FEE_DISCOUNT) * (1.0 + GST)


# --- fills (which side of the book you cross) ------------------------------- #
def entry_fill(side: Side, bid: float, ask: float, fallback: float) -> float:
    """At placement: a buy lifts the ask, a sell hits the bid. 0/None → fallback."""
    return (ask or fallback) if side == "buy" else (bid or fallback)


def exit_fill(side: Side, bid: float, ask: float, fallback: float) -> float:
    """At close: a long is sold at the bid, a short is bought back at the ask."""
    return (bid or fallback) if side == "buy" else (ask or fallback)


def spread(bid: float, ask: float) -> float:
    return max(ask - bid, 0.0)


# --- P&L / slippage / greeks ------------------------------------------------ #
def leg_pnl(side: Side, qty: float, cv: float, entry: float, mark: float) -> float:
    """USD P&L for one leg at `mark` (long gains as mark>entry, short the reverse)."""
    return leg_sign(side) * (mark - entry) * qty * cv


def leg_entry_slippage(entry: float, mark_at_entry: float, qty: float, cv: float) -> float:
    """Cost of crossing the spread at entry (USD) — |fill − mark| · size."""
    return abs(entry - mark_at_entry) * qty * cv


def leg_greek_contrib(side: Side, qty: float, cv: float, per_option_greek: float) -> float:
    """Net-greek contribution: signed_qty · cv · per-option greek."""
    return leg_sign(side) * qty * cv * per_option_greek


# --- aggregation over a strategy's legs ------------------------------------- #
@dataclass(frozen=True)
class LegQuote:
    side: Side
    qty: float
    cv: float
    entry: float
    mark: float


def net_pnl(legs: Iterable[LegQuote]) -> float:
    return sum(leg_pnl(l.side, l.qty, l.cv, l.entry, l.mark) for l in legs)


@dataclass(frozen=True)
class LegGreeks:
    side: Side
    qty: float
    cv: float
    delta: float
    theta: float
    vega: float


def net_greeks(legs: Iterable[LegGreeks]) -> dict[str, float]:
    d = t = v = 0.0
    for l in legs:
        k = leg_sign(l.side) * l.qty * l.cv
        d += k * l.delta
        t += k * l.theta
        v += k * l.vega
    return {"delta": d, "theta": t, "vega": v}
