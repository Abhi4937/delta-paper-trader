"""TDD for the PayoffEngine: expiry payoff, breakevens, max P/L.

Cases use contract_value=1 and hand-computed values.
"""

import numpy as np

from app.engines.payoff import Leg, payoff_at_expiry, payoff_profile


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
