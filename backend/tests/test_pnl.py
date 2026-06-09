"""TDD for the PnL engine: per-leg & net, realized/unrealized, fees."""

import numpy as np

from app.engines.pnl import PositionLeg, leg_pnl, strategy_pnl


def test_long_leg_unrealized_profit() -> None:
    leg = PositionLeg(side="long", qty=1, avg_entry=5.0, contract_value=1.0)
    r = leg_pnl(leg, mark=8.0)
    assert np.isclose(r.unrealized, 3.0)
    assert np.isclose(r.total, 3.0)


def test_short_leg_unrealized_loss() -> None:
    leg = PositionLeg(side="short", qty=1, avg_entry=5.0, contract_value=1.0)
    r = leg_pnl(leg, mark=8.0)
    assert np.isclose(r.unrealized, -3.0)


def test_qty_and_contract_value_scale() -> None:
    leg = PositionLeg(side="long", qty=2, avg_entry=100.0, contract_value=0.001)
    r = leg_pnl(leg, mark=150.0)
    # (150-100) * 2 * 0.001 = 0.1
    assert np.isclose(r.unrealized, 0.1)


def test_realized_and_fees() -> None:
    leg = PositionLeg(
        side="long", qty=1, avg_entry=5.0, contract_value=1.0,
        realized=10.0, fees_paid=0.5,
    )
    r = leg_pnl(leg, mark=5.0)
    # unrealized 0, realized 10, minus fees 0.5
    assert np.isclose(r.unrealized, 0.0)
    assert np.isclose(r.realized, 10.0)
    assert np.isclose(r.total, 9.5)


def test_strategy_net_pnl() -> None:
    # Short call spread: short 62000CE @611 (mark 588) + long 63000CE @430 (mark 441)
    legs = [
        PositionLeg(side="short", qty=1, avg_entry=611.0, contract_value=1.0),
        PositionLeg(side="long", qty=1, avg_entry=430.0, contract_value=1.0),
    ]
    marks = [588.0, 441.0]
    s = strategy_pnl(legs, marks)
    # short: -(588-611) = +23 ; long: (441-430) = +11 ; net +34
    assert np.isclose(s.net_unrealized, 34.0)
    assert np.isclose(s.net_total, 34.0)
    assert len(s.legs) == 2
