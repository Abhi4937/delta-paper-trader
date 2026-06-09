"""Golden-value tests for the ported money model. These numbers match Delta's own
fee calculator and the client engine the model was validated against @10 lots —
they are the regression guard for the server-side port."""

import pytest

from app.engines.money import (
    FEE_SCHEDULES,
    LegGreeks,
    LegQuote,
    entry_fill,
    exit_fill,
    leg_entry_slippage,
    leg_fee,
    leg_pnl,
    net_greeks,
    net_pnl,
    spread,
)


# --- fees (Options Carnival in force) --------------------------------------- #
def test_fee_notional_bound() -> None:
    # price 2000, spot 60000, cv 0.001, 10 lots → notional 600, premium 20
    # base = min(0.0001*600=0.06, 0.035*20=0.70) = 0.06 ; +18% GST
    assert leg_fee(2000, 60000, 0.001, 10) == pytest.approx(0.0708)


def test_fee_premium_cap_bound() -> None:
    # cheap option (price 50) → premium cap binds: base = 0.035*0.5 = 0.0175
    assert leg_fee(50, 60000, 0.001, 10) == pytest.approx(0.02065)


def test_fee_standard_schedule() -> None:
    std = FEE_SCHEDULES["standard"]
    # base = min(0.0003*600=0.18, 0.10*20=2.0)=0.18 ; ×1.18
    assert leg_fee(2000, 60000, 0.001, 10, std) == pytest.approx(0.2124)


# --- P&L / slippage --------------------------------------------------------- #
def test_leg_pnl_long_and_short() -> None:
    assert leg_pnl("buy", 10, 0.001, 2000, 2100) == pytest.approx(1.0)
    assert leg_pnl("sell", 10, 0.001, 2000, 2100) == pytest.approx(-1.0)


def test_entry_slippage() -> None:
    # bought at 2010 vs mark 2000 → 10 pts × 10 lots × 0.001
    assert leg_entry_slippage(2010, 2000, 10, 0.001) == pytest.approx(0.1)


def test_net_pnl_two_legs() -> None:
    legs = [
        LegQuote("buy", 1, 0.001, 1200, 1300),  # +0.1
        LegQuote("sell", 1, 0.001, 1000, 900),  # +0.1
    ]
    assert net_pnl(legs) == pytest.approx(0.2)


# --- fills (which side of the book) ----------------------------------------- #
def test_entry_fill_crosses_correct_side() -> None:
    assert entry_fill("buy", 99, 101, 100) == 101  # buy lifts the ask
    assert entry_fill("sell", 99, 101, 100) == 99  # sell hits the bid


def test_exit_fill_crosses_correct_side() -> None:
    assert exit_fill("buy", 99, 101, 100) == 99  # close long at bid
    assert exit_fill("sell", 99, 101, 100) == 101  # close short at ask


def test_fill_falls_back_when_quote_missing() -> None:
    assert entry_fill("buy", 0, 0, 100) == 100  # no book → fallback
    assert exit_fill("sell", 0, 0, 100) == 100


def test_spread() -> None:
    assert spread(99, 101) == pytest.approx(2.0)
    assert spread(101, 99) == 0.0  # never negative


# --- net greeks aggregation ------------------------------------------------- #
def test_net_greeks_signs_and_sum() -> None:
    legs = [
        LegGreeks("buy", 1, 0.001, 0.5, -1.0, 2.0),  # long: +signs
        LegGreeks("sell", 1, 0.001, 0.5, -1.0, 2.0),  # short: flips signs → cancels delta/vega, theta +
    ]
    g = net_greeks(legs)
    assert g["delta"] == pytest.approx(0.0)
    assert g["vega"] == pytest.approx(0.0)
    assert g["theta"] == pytest.approx(0.0)  # -1 and +1 cancel here


def test_net_greeks_short_collects_theta() -> None:
    g = net_greeks([LegGreeks("sell", 1, 0.001, 0.5, -1.0, 2.0)])
    assert g["delta"] < 0 and g["vega"] < 0 and g["theta"] > 0  # short option: +theta
