"""TDD for the PayoffEngine: expiry payoff, breakevens, max P/L.

Cases use contract_value=1 and hand-computed values.
"""

import numpy as np

from app.engines.payoff import (
    Leg,
    ScenarioLeg,
    net_greeks,
    payoff_at_expiry,
    payoff_profile,
    projected_pnl,
)


def test_long_call_payoff() -> None:
    # Long 100 call @ premium 5. P/L = max(S-100,0) - 5.
    leg = Leg(kind="call", side="long", strike=100, entry_price=5, qty=1)
    prices = np.array([90.0, 100.0, 105.0, 110.0])
    pl = payoff_at_expiry([leg], prices)
    assert np.allclose(pl, [-5.0, -5.0, 0.0, 5.0])


def test_short_put_payoff() -> None:
    # Short 100 put @ premium 5. P/L = 5 - max(100-S,0).
    leg = Leg(kind="put", side="short", strike=100, entry_price=5, qty=1)
    prices = np.array([90.0, 95.0, 100.0, 110.0])
    pl = payoff_at_expiry([leg], prices)
    assert np.allclose(pl, [-5.0, 0.0, 5.0, 5.0])


def test_long_future_payoff() -> None:
    # Long future entered at 100. P/L = S - 100.
    leg = Leg(kind="future", side="long", strike=0, entry_price=100, qty=1)
    prices = np.array([90.0, 100.0, 110.0])
    pl = payoff_at_expiry([leg], prices)
    assert np.allclose(pl, [-10.0, 0.0, 10.0])


def test_bull_call_spread_profile() -> None:
    # Long 100 call @5, short 110 call @2. Net debit 3.
    # Max loss -3 (S<=100), max profit +7 (S>=110), BE at 103.
    legs = [
        Leg(kind="call", side="long", strike=100, entry_price=5, qty=1),
        Leg(kind="call", side="short", strike=110, entry_price=2, qty=1),
    ]
    prof = payoff_profile(legs, spot=105, lo=80, hi=130, points=5001)
    assert np.isclose(prof.max_loss, -3.0, atol=1e-2)
    assert np.isclose(prof.max_profit, 7.0, atol=1e-2)
    assert len(prof.breakevens) == 1
    assert np.isclose(prof.breakevens[0], 103.0, atol=0.05)


def test_qty_and_contract_value_scale() -> None:
    leg = Leg(kind="call", side="long", strike=100, entry_price=5, qty=3)
    prices = np.array([110.0])
    pl = payoff_at_expiry([leg], prices, contract_value=10)
    # (max(110-100,0) - 5) * 3 * 10 = 5 * 30 = 150
    assert np.isclose(pl[0], 150.0)


# --- Analyse Payoff: projected curve + net greeks --------------------------- #
# Each ScenarioLeg carries its OWN time-to-expiry (`t_years`, from "now"). A scenario
# is evaluated at `elapsed_years` into the future; each leg's residual life is
# max(t_years − elapsed_years, 0). This supports calendars/diagonals (per-leg expiry).
def test_projected_at_full_elapsed_equals_intrinsic() -> None:
    # elapsed == the leg's own expiry → residual 0 → intrinsic − entry.
    legs = [ScenarioLeg("call", "long", 1, 100, 5, 0.5, 1.0, t_years=0.25)]
    prices = np.array([90.0, 100.0, 110.0])
    proj = projected_pnl(legs, prices, elapsed_years=0.25, iv_shift=0.0)
    assert np.allclose(proj, [-5.0, -5.0, 5.0])


def test_projected_has_time_value_before_expiry() -> None:
    # A long option is worth MORE than intrinsic before expiry → P/L above expiry.
    legs = [ScenarioLeg("call", "long", 1, 100, 5, 0.5, 1.0, t_years=0.25)]
    prices = np.array([100.0])
    at_expiry = projected_pnl(legs, prices, elapsed_years=0.25, iv_shift=0.0)[0]
    now = projected_pnl(legs, prices, elapsed_years=0.0, iv_shift=0.0)[0]
    assert now > at_expiry


def test_calendar_not_flat_at_front_expiry() -> None:
    # Same-strike calendar: short front call (7d) + long back call (30d). At the FRONT
    # expiry the back leg still has time value → a tent peaking near the strike. The old
    # single-t_years engine expired BOTH legs → identical intrinsics cancel → flat line
    # (the "no payoff graph" bug). Per-leg expiry must restore the curve.
    front = ScenarioLeg("call", "short", 1, 100, 2.0, 0.5, 1.0, t_years=7 / 365)
    back = ScenarioLeg("call", "long", 1, 100, 4.0, 0.5, 1.0, t_years=30 / 365)
    legs = [front, back]
    prices = np.linspace(80.0, 120.0, 201)
    elapsed = min(leg.t_years for leg in legs)  # the front expiry
    curve = projected_pnl(legs, prices, elapsed_years=elapsed, iv_shift=0.0)
    assert float(curve.max() - curve.min()) > 0.1  # NOT flat
    peak = float(prices[int(np.argmax(curve))])
    assert abs(peak - 100.0) < 5.0  # calendar tent peaks near the strike


def test_net_greeks_long_straddle_is_delta_neutral_at_atm() -> None:
    legs = [
        ScenarioLeg("call", "long", 1, 100, 4, 0.5, 1.0, t_years=0.1),
        ScenarioLeg("put", "long", 1, 100, 4, 0.5, 1.0, t_years=0.1),
    ]
    g = net_greeks(legs, spot=100, elapsed_years=0.0, iv_shift=0.0)
    assert abs(g["delta"]) < 0.1  # ATM-spot straddle ≈ delta-neutral (small ½σ²T residual)
    assert g["vega"] > 0 and g["gamma"] > 0  # long vol/gamma
    assert g["theta"] < 0  # pays time decay


def test_net_greeks_short_call_signs() -> None:
    legs = [ScenarioLeg("call", "short", 1, 100, 4, 0.5, 1.0, t_years=0.1)]
    g = net_greeks(legs, spot=100, elapsed_years=0.0, iv_shift=0.0)
    assert g["delta"] < 0 and g["vega"] < 0 and g["theta"] > 0  # short call collects theta
