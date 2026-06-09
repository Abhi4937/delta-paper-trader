"""Risk/exit decision logic — ported from frontend/src/lib/exit.ts.

Pure: given current leg PnLs + stops + a whole-feed stale flag, decide what (if
anything) to auto-exit. Auto-exit is SUSPENDED on stale data (never act on bad
data); combined net-capital stop closes the whole strategy; per-leg TP/SL act on
leg PnL with a leg/strategy close-scope.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class ExitLeg:
    id: str
    pnl: float
    target_pnl: float | None
    stop_pnl: float | None
    auto_exit: bool
    close_scope: Literal["leg", "strategy"]
    status: Literal["open", "closed"]


@dataclass(frozen=True)
class CombinedStop:
    loss_amount: float | None  # absolute USD floor (magnitude)
    loss_pct_of_margin: float | None  # % of reserved margin (magnitude)


@dataclass(frozen=True)
class ExitDecision:
    kind: Literal["none", "suspended", "close-strategy", "close-legs"]
    reason: str = ""
    leg_ids: tuple[str, ...] = ()


def combined_floor(stop: CombinedStop | None, margin: float) -> float | None:
    if stop is None:
        return None
    if stop.loss_amount is not None:
        return -abs(stop.loss_amount)
    if stop.loss_pct_of_margin is not None:
        return -(margin * abs(stop.loss_pct_of_margin)) / 100
    return None


def evaluate_exit(
    legs: list[ExitLeg],
    *,
    net_pnl: float,
    margin: float,
    combined_stop: CombinedStop | None,
    combined_auto_exit: bool,
    stale: bool,
) -> ExitDecision:
    open_legs = [lg for lg in legs if lg.status == "open"]
    if not open_legs:
        return ExitDecision("none")

    # 1) stale feed → suspend ALL auto-exit
    if stale:
        return ExitDecision("suspended")

    # 2) combined net-capital stop → close the whole strategy
    if combined_auto_exit:
        floor = combined_floor(combined_stop, margin)
        if floor is not None and net_pnl <= floor:
            return ExitDecision("close-strategy", reason="combined SL")

    # 3) per-leg TP/SL (on leg PnL)
    triggered = [
        lg
        for lg in open_legs
        if lg.auto_exit
        and (
            (lg.target_pnl is not None and lg.pnl >= lg.target_pnl)
            or (lg.stop_pnl is not None and lg.pnl <= lg.stop_pnl)
        )
    ]
    if not triggered:
        return ExitDecision("none")
    if any(lg.close_scope == "strategy" for lg in triggered):
        return ExitDecision("close-strategy", reason="leg TP/SL")
    return ExitDecision("close-legs", reason="leg TP/SL", leg_ids=tuple(lg.id for lg in triggered))
