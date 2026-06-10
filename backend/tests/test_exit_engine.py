"""Edge-case tests for the risk/exit decision logic (app/sim/exit_engine.py).

The critical guarantee: auto-exit is SUSPENDED on stale data and NEVER acts on
bad marks, even when stops are breached. Combined net-capital stop closes the
whole strategy; per-leg TP/SL act on leg PnL with a leg/strategy close-scope.
"""

from typing import Literal

from app.sim.exit_engine import CombinedStop, ExitLeg, evaluate_exit


def _leg(
    id: str = "L1",
    pnl: float = 0.0,
    target_pnl: float | None = None,
    stop_pnl: float | None = None,
    auto_exit: bool = True,
    close_scope: Literal["leg", "strategy"] = "leg",
    status: Literal["open", "closed"] = "open",
) -> ExitLeg:
    return ExitLeg(
        id=id,
        pnl=pnl,
        target_pnl=target_pnl,
        stop_pnl=stop_pnl,
        auto_exit=auto_exit,
        close_scope=close_scope,
        status=status,
    )


# --- stale feed: never act on bad data ------------------------------------- #
def test_stale_suspends_even_when_combined_and_leg_stops_breached() -> None:
    # Both a combined SL and a per-leg SL are breached — but stale wins.
    leg = _leg(pnl=-9999.0, stop_pnl=-100.0)
    d = evaluate_exit(
        [leg],
        net_pnl=-9999.0,
        margin=1000.0,
        combined_stop=CombinedStop(loss_amount=100.0, loss_pct_of_margin=None),
        combined_auto_exit=True,
        stale=True,
    )
    assert d.kind == "suspended"


# --- combined net-capital stop --------------------------------------------- #
def test_combined_loss_amount_triggers_close_strategy() -> None:
    leg = _leg(pnl=-500.0)
    d = evaluate_exit(
        [leg],
        net_pnl=-500.0,
        margin=1000.0,
        combined_stop=CombinedStop(loss_amount=400.0, loss_pct_of_margin=None),
        combined_auto_exit=True,
        stale=False,
    )
    assert d.kind == "close-strategy"
    assert d.reason == "combined SL"


def test_combined_loss_pct_of_margin_triggers_close_strategy() -> None:
    # 50% of 1000 margin -> floor -500; net_pnl -600 is below it.
    leg = _leg(pnl=-600.0)
    d = evaluate_exit(
        [leg],
        net_pnl=-600.0,
        margin=1000.0,
        combined_stop=CombinedStop(loss_amount=None, loss_pct_of_margin=50.0),
        combined_auto_exit=True,
        stale=False,
    )
    assert d.kind == "close-strategy"
    assert d.reason == "combined SL"


def test_combined_not_triggered_when_net_pnl_above_floor() -> None:
    # net_pnl -300 is above the -400 floor -> no combined trigger (and no leg trigger).
    leg = _leg(pnl=-300.0)
    d = evaluate_exit(
        [leg],
        net_pnl=-300.0,
        margin=1000.0,
        combined_stop=CombinedStop(loss_amount=400.0, loss_pct_of_margin=None),
        combined_auto_exit=True,
        stale=False,
    )
    assert d.kind == "none"


def test_combined_auto_exit_false_does_not_trigger() -> None:
    leg = _leg(pnl=-500.0)
    d = evaluate_exit(
        [leg],
        net_pnl=-500.0,
        margin=1000.0,
        combined_stop=CombinedStop(loss_amount=400.0, loss_pct_of_margin=None),
        combined_auto_exit=False,
        stale=False,
    )
    assert d.kind == "none"


# --- per-leg TP / SL -------------------------------------------------------- #
def test_leg_take_profit_close_leg_scope() -> None:
    leg = _leg(id="A", pnl=150.0, target_pnl=100.0, close_scope="leg")
    d = evaluate_exit(
        [leg],
        net_pnl=150.0,
        margin=1000.0,
        combined_stop=None,
        combined_auto_exit=False,
        stale=False,
    )
    assert d.kind == "close-legs"
    assert d.leg_ids == ("A",)
    assert d.reason == "leg TP/SL"


def test_leg_stop_loss_close_leg_scope() -> None:
    leg = _leg(id="B", pnl=-150.0, stop_pnl=-100.0, close_scope="leg")
    d = evaluate_exit(
        [leg],
        net_pnl=-150.0,
        margin=1000.0,
        combined_stop=None,
        combined_auto_exit=False,
        stale=False,
    )
    assert d.kind == "close-legs"
    assert d.leg_ids == ("B",)


def test_leg_trigger_with_strategy_scope_closes_strategy() -> None:
    leg = _leg(id="C", pnl=-150.0, stop_pnl=-100.0, close_scope="strategy")
    d = evaluate_exit(
        [leg],
        net_pnl=-150.0,
        margin=1000.0,
        combined_stop=None,
        combined_auto_exit=False,
        stale=False,
    )
    assert d.kind == "close-strategy"
    assert d.reason == "leg TP/SL"


def test_only_triggered_legs_are_returned() -> None:
    triggered = _leg(id="hit", pnl=-150.0, stop_pnl=-100.0, close_scope="leg")
    safe = _leg(id="safe", pnl=10.0, stop_pnl=-100.0, target_pnl=500.0, close_scope="leg")
    d = evaluate_exit(
        [triggered, safe],
        net_pnl=-140.0,
        margin=1000.0,
        combined_stop=None,
        combined_auto_exit=False,
        stale=False,
    )
    assert d.kind == "close-legs"
    assert d.leg_ids == ("hit",)


def test_leg_auto_exit_false_never_triggers() -> None:
    leg = _leg(pnl=-500.0, stop_pnl=-100.0, auto_exit=False)
    d = evaluate_exit(
        [leg],
        net_pnl=-500.0,
        margin=1000.0,
        combined_stop=None,
        combined_auto_exit=False,
        stale=False,
    )
    assert d.kind == "none"


# --- no open legs ----------------------------------------------------------- #
def test_all_closed_legs_returns_none() -> None:
    leg = _leg(pnl=-500.0, stop_pnl=-100.0, status="closed")
    d = evaluate_exit(
        [leg],
        net_pnl=-500.0,
        margin=1000.0,
        combined_stop=CombinedStop(loss_amount=100.0, loss_pct_of_margin=None),
        combined_auto_exit=True,
        stale=False,
    )
    assert d.kind == "none"


def test_empty_leg_list_returns_none() -> None:
    d = evaluate_exit(
        [],
        net_pnl=0.0,
        margin=1000.0,
        combined_stop=None,
        combined_auto_exit=True,
        stale=False,
    )
    assert d.kind == "none"
