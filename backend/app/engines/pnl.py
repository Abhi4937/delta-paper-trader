"""PnL engine: per-leg and net position P/L from live marks.

Realized vs unrealized, with fees threaded in as explicit inputs. The exact Delta
taker/maker fee *schedule* is a separate calibrated module (we do not guess money
formulas — see CLAUDE.md); this engine simply nets the fees it is given.

All values are in the contract's quote currency; convert to INR upstream on Delta's
basis and store the basis for reproducibility.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

LegSide = Literal["long", "short"]


@dataclass(frozen=True)
class PositionLeg:
    side: LegSide
    qty: float
    avg_entry: float
    contract_value: float = 1.0
    realized: float = 0.0  # P/L already booked from partial/closed fills
    fees_paid: float = 0.0  # total fees attributed to this leg

    @property
    def sign(self) -> int:
        return 1 if self.side == "long" else -1


@dataclass(frozen=True)
class LegPnL:
    unrealized: float
    realized: float
    fees: float
    total: float


@dataclass(frozen=True)
class StrategyPnL:
    legs: list[LegPnL]
    net_unrealized: float
    net_realized: float
    net_fees: float
    net_total: float


def leg_pnl(leg: PositionLeg, mark: float) -> LegPnL:
    """P/L for one open leg at the current mark price."""
    unrealized = leg.sign * (mark - leg.avg_entry) * leg.qty * leg.contract_value
    total = unrealized + leg.realized - leg.fees_paid
    return LegPnL(
        unrealized=unrealized,
        realized=leg.realized,
        fees=leg.fees_paid,
        total=total,
    )


def strategy_pnl(legs: list[PositionLeg], marks: list[float]) -> StrategyPnL:
    """Net P/L across all legs of a strategy.

    `marks[i]` is the current mark for `legs[i]`.
    """
    if len(legs) != len(marks):
        raise ValueError("legs and marks must have the same length")
    results = [leg_pnl(leg, mark) for leg, mark in zip(legs, marks, strict=True)]
    return StrategyPnL(
        legs=results,
        net_unrealized=sum(r.unrealized for r in results),
        net_realized=sum(r.realized for r in results),
        net_fees=sum(r.fees for r in results),
        net_total=sum(r.total for r in results),
    )
