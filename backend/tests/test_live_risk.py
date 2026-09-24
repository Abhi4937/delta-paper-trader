import time

from app.live.risk import (
    RiskLeg,
    check_sl_vs_liquidation,
    ioc_limit_price,
    margin_level,
    native_stop_price,
)


def test_native_stop_short_above_entry_long_below():
    # short 100 contracts x 0.001 cv, entry 200: -$10 loss => premium 200 + 10/0.1 = 300
    assert native_stop_price("sell", 200, 100, 0.001, -10, 0.5) == 300
    assert native_stop_price("buy", 200, 100, 0.001, -10, 0.5) == 100
    # long whose whole premium is inside the stop: no stop possible
    assert native_stop_price("buy", 50, 100, 0.001, -10, 0.5) is None
    assert native_stop_price("sell", 200, 100, 0.001, None, 0.5) is None


def test_native_stop_never_loses_more_than_basket():
    px = native_stop_price("sell", 120, 10, 0.001, -25, 0.1)
    assert (px - 120) * 10 * 0.001 >= 25 - 1e-9
    assert (px - 0.1 - 120) * 10 * 0.001 < 25  # rounded by at most one tick


def test_triggers_fire_on_the_right_side_of_the_mark():
    from app.live.risk import sl_crossed, tp_crossed

    # bought at 200: SL 150 fires when the premium FALLS to it, target 300 when it RISES
    assert (
        not sl_crossed("buy", 151, 150)
        and sl_crossed("buy", 150, 150)
        and sl_crossed("buy", 90, 150)
    )
    assert not tp_crossed("buy", 299, 300) and tp_crossed("buy", 300, 300)
    # sold at 200: SL 400 fires when the premium RISES to it, target 80 when it FALLS
    assert (
        not sl_crossed("sell", 399, 400)
        and sl_crossed("sell", 400, 400)
        and sl_crossed("sell", 900, 400)
    )
    assert not tp_crossed("sell", 81, 80) and tp_crossed("sell", 80, 80)
    assert not sl_crossed("sell", 10_000, None)


def test_crossed_or_too_close_triggers_are_refused():
    from app.live.risk import check_trigger

    # sold leg, mark 200: SL must be ABOVE the mark (by >= 1% = 2.0)
    assert check_trigger("sell", "SL", 400, 200, 0.1) is None
    assert "above" in check_trigger("sell", "SL", 190, 200, 0.1)  # already crossed
    assert "above" in check_trigger("sell", "SL", 201, 200, 0.1)  # inside the buffer
    # bought leg, mark 200: SL below, target above
    assert check_trigger("buy", "SL", 150, 200, 0.1) is None
    assert "below" in check_trigger("buy", "SL", 250, 200, 0.1)
    assert check_trigger("buy", "Target", 300, 200, 0.1) is None
    assert "above" in check_trigger("buy", "Target", 199, 200, 0.1)
    assert check_trigger("sell", "Target", 80, 200, 0.1) is None
    assert "positive" in check_trigger("sell", "Target", 0, 200, 0.1)


def test_bracket_sl_is_the_tighter_of_leg_sl_and_basket():
    from app.live.risk import bracket_sl_price

    # sold 10 lots at 200; basket $1 => this leg alone loses $1 at 300
    assert bracket_sl_price("sell", 200, 10, 0.001, 400, 1.0, 0.1) == 300  # basket tighter
    assert bracket_sl_price("sell", 200, 10, 0.001, 250, 1.0, 0.1) == 250  # leg tighter
    assert bracket_sl_price("sell", 200, 10, 0.001, 400, None, 0.1) == 400
    assert bracket_sl_price("buy", 200, 10, 0.001, 150, 1.0, 0.1) == 150  # 150 > basket's 100
    assert bracket_sl_price("buy", 200, 10, 0.001, None, None, 0.1) is None


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


def test_selling_credits_premium_and_buying_debits_it():
    from app.live.risk import balance_after_fill

    short = RiskLeg("call", "sell", 100, 86600, 3, 0.001, 271.0, 275.7)
    long_ = RiskLeg("put", "buy", 10, 81200, 3, 0.001, 205.0, 200.2)
    assert round(balance_after_fill(452.08, [short]), 2) == 479.18  # +27.10 premium
    assert round(balance_after_fill(452.08, [long_]), 2) == 450.03  # -2.05 paid
