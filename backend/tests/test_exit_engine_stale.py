"""Stale-aware hard-stop SL (3B). Default behavior is unchanged (suspend on stale);
a strategy can opt in (stale_hard_stop) so its LOSS stops keep firing against the
last-known marks during a feed outage, after a grace window. Take-profit never fires
on stale data (don't bank a winner on a frozen price)."""

from app.sim.exit_engine import CombinedStop, ExitLeg, evaluate_exit


def _leg(
    pnl: float, *, stop: float | None = None, target: float | None = None,
    scope: str = "leg", auto: bool = True, status: str = "open", lid: str = "L1",
) -> ExitLeg:
    return ExitLeg(
        id=lid, pnl=pnl, target_pnl=target, stop_pnl=stop,
        auto_exit=auto, close_scope=scope, status=status,  # type: ignore[arg-type]
    )


def test_stale_without_toggle_suspends() -> None:
    d = evaluate_exit(
        [_leg(-100, stop=-50)], net_pnl=-100, margin=1000,
        combined_stop=None, combined_auto_exit=False, stale=True,
    )
    assert d.kind == "suspended"


def test_stale_hard_stop_fires_per_leg_stop() -> None:
    d = evaluate_exit(
        [_leg(-100, stop=-50)], net_pnl=-100, margin=1000,
        combined_stop=None, combined_auto_exit=False, stale=True,
        stale_hard_stop=True, stale_grace_elapsed=True,
    )
    assert d.kind == "close-legs"
    assert "stale hard-stop" in d.reason
    assert d.leg_ids == ("L1",)


def test_stale_hard_stop_fires_combined_sl() -> None:
    d = evaluate_exit(
        [_leg(-100)], net_pnl=-100, margin=1000,
        combined_stop=CombinedStop(50.0, None), combined_auto_exit=True, stale=True,
        stale_hard_stop=True, stale_grace_elapsed=True,
    )
    assert d.kind == "close-strategy"
    assert "stale hard-stop" in d.reason


def test_stale_hard_stop_ignores_take_profit() -> None:
    # target hit, NO stop → must NOT exit on a frozen price during stale
    d = evaluate_exit(
        [_leg(100, target=50)], net_pnl=100, margin=1000,
        combined_stop=None, combined_auto_exit=False, stale=True,
        stale_hard_stop=True, stale_grace_elapsed=True,
    )
    assert d.kind == "none"


def test_glitch_within_grace_still_suspends() -> None:
    d = evaluate_exit(
        [_leg(-100, stop=-50)], net_pnl=-100, margin=1000,
        combined_stop=None, combined_auto_exit=False, stale=True,
        stale_hard_stop=True, stale_grace_elapsed=False,
    )
    assert d.kind == "suspended"


def test_stale_hard_stop_no_breach_keeps_monitoring() -> None:
    # opted in + grace, nothing breached → keep watching (NOT suspended)
    d = evaluate_exit(
        [_leg(-10, stop=-50)], net_pnl=-10, margin=1000,
        combined_stop=None, combined_auto_exit=False, stale=True,
        stale_hard_stop=True, stale_grace_elapsed=True,
    )
    assert d.kind == "none"


def test_fresh_take_profit_fires_normally() -> None:
    d = evaluate_exit(
        [_leg(100, target=50)], net_pnl=100, margin=1000,
        combined_stop=None, combined_auto_exit=False, stale=False,
    )
    assert d.kind == "close-legs"
    assert "stale" not in d.reason
