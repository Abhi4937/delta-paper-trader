import time

from app.live.risk import (
    RiskLeg,
    check_sl_vs_liquidation,
    effective_stop_pnl,
    ioc_limit_price,
    margin_level,
    native_stop_price,
)


def test_effective_stop_takes_tighter_and_falls_back_to_basket():
    assert effective_stop_pnl(None, None) is None
    assert effective_stop_pnl(None, 50) == -50
    assert effective_stop_pnl(-30, 50) == -30
    assert effective_stop_pnl(-80, 50) == -50


def test_native_stop_short_above_entry_long_below():
    # short 100 contracts x 0.001 cv, entry 200: -$10 loss => premium 200 + 10/0.1 = 300
    assert native_stop_price("sell", 200, 100, 0.001, -10, 0.5) == 300
    assert native_stop_price("buy", 200, 100, 0.001, -10, 0.5) == 100
    # long whose whole premium is inside the stop: no stop possible
    assert native_stop_price("buy", 50, 100, 0.001, -10, 0.5) is None
    assert native_stop_price("sell", 200, 100, 0.001, None, 0.5) is None


def test_native_stop_never_loses_more_than_basket():
    stop = effective_stop_pnl(None, 25)
    px = native_stop_price("sell", 120, 10, 0.001, stop, 0.1)
    assert (px - 120) * 10 * 0.001 >= 25 - 1e-9
    assert (px - 0.1 - 120) * 10 * 0.001 < 25  # rounded by at most one tick


def test_ioc_band_widens_and_rounds_against_us():
    assert ioc_limit_price("buy", 100, 0.05, 0.5) == 105
    assert ioc_limit_price("buy", 100, 0.10, 0.5) == 110
    assert ioc_limit_price("sell", 100, 0.05, 0.5) == 95
    assert ioc_limit_price("sell", 0.4, 0.40, 0.5) == 0.5  # never below one tick


def test_margin_levels():
    assert margin_level(None) is None
    assert margin_level(0.59) is None
    assert margin_level(0.6) == "warning"
    assert margin_level(0.85) == "critical"
    assert margin_level(0.95) == "emergency"


def _strangle(qty, stop=None):
    # BTC ~ 100k, short 105k call / 95k put, 7 DTE
    return [
        RiskLeg("call", "sell", qty, 105_000, 7, 0.001, 900, 900, stop),
        RiskLeg("put", "sell", qty, 95_000, 7, 0.001, 900, 900, stop),
    ]


def test_small_size_with_tight_stop_is_green():
    r = check_sl_vs_liquidation(_strangle(10), spot=100_000, balance=5_000, basket_floor=20)
    assert r.verdict == "green", r.reason
    assert r.sl_spot is not None
    assert r.max_scale >= 1


def test_oversized_with_no_stop_is_red():
    r = check_sl_vs_liquidation(_strangle(2_000), spot=100_000, balance=2_000, basket_floor=None)
    assert r.verdict == "red", r.reason
    assert r.liq_spot is not None
    assert r.max_scale == 0


def test_stop_too_wide_for_capital_is_not_green():
    # stop 5,000 USD on a 1,000 USD account: liquidation must come first
    r = check_sl_vs_liquidation(_strangle(500), spot=100_000, balance=1_000, basket_floor=5_000)
    assert r.verdict == "red", r.reason


def test_check_is_fast_enough_for_the_sync_loop():
    t = time.perf_counter()
    check_sl_vs_liquidation(_strangle(10), spot=100_000, balance=5_000, basket_floor=20)
    assert time.perf_counter() - t < 2.0
