"""Net greeks aggregation: position greeks scale with lots exactly ONCE — the
per-contract greek (e.g. delta 0.5) is lot-independent; the net/position greek is
signed_qty x contract_value x greek. Pins that there is no double-count by lots
(the thing the owner suspected). Validated absolute numbers vs a real Delta account
are a separate task (see memory: position-greeks-unvalidated)."""

from app.engines.money import LegGreeks, net_greeks


def test_net_delta_scales_with_lots_exactly_once() -> None:
    # one long call, per-contract delta 0.5, cv 0.001
    one = net_greeks([LegGreeks("buy", 1, 0.001, 0.5, -1.0, 2.0)])
    ten = net_greeks([LegGreeks("buy", 10, 0.001, 0.5, -1.0, 2.0)])
    assert one["delta"] == 0.5 * 1 * 0.001
    assert ten["delta"] == 0.5 * 10 * 0.001
    # 10 lots = exactly 10x one lot — linear, not squared (no double-count)
    assert ten["delta"] == 10 * one["delta"]


def test_sell_flips_sign() -> None:
    buy = net_greeks([LegGreeks("buy", 2, 0.001, 0.4, -1.0, 2.0)])
    sell = net_greeks([LegGreeks("sell", 2, 0.001, 0.4, -1.0, 2.0)])
    assert sell["delta"] == -buy["delta"]
    assert sell["theta"] == -buy["theta"]
    assert sell["vega"] == -buy["vega"]


def test_net_is_sum_of_legs() -> None:
    # a strangle: short call + short put — net delta near flat, thetas add
    legs = [
        LegGreeks("sell", 5, 0.001, 0.30, -1.2, 3.0),
        LegGreeks("sell", 5, 0.001, -0.28, -1.1, 2.8),
    ]
    out = net_greeks(legs)
    assert out["delta"] == -(5 * 0.001 * 0.30) - (5 * 0.001 * -0.28)
    assert out["theta"] == -(5 * 0.001 * -1.2) - (5 * 0.001 * -1.1)
