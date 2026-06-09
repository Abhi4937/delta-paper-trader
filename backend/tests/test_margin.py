"""TDD for the local portfolio-margin engine (ADR 0002).

Verifies the priced sub-pieces against known values + structural/relative
properties of Delta's methodology. Exact rupee match is achieved later by
calibration against the live estimator.
"""

import math

from app.engines.margin import (
    MarginLeg,
    MarginParams,
    bs_price,
    compute_margin,
    defined_max_loss,
    iv_shock,
    om_pct,
    price_shock_span,
    scenarios,
    vol_down_span,
    vol_up_span,
)

P = MarginParams()


def test_bs_price_known_value() -> None:
    # S=K=100, T=1, sigma=0.2, r=0  -> call = put = 7.9656
    call = bs_price("call", 100, 100, 1.0, 0.2)
    put = bs_price("put", 100, 100, 1.0, 0.2)
    assert abs(call - 7.9656) < 1e-3
    assert abs(put - 7.9656) < 1e-3


def test_bs_intrinsic_at_zero_time() -> None:
    assert abs(bs_price("call", 110, 100, 0.0, 0.5) - 10.0) < 1e-9
    assert abs(bs_price("put", 90, 100, 0.0, 0.5) - 10.0) < 1e-9


def test_price_shock_span_scales_and_caps() -> None:
    assert abs(price_shock_span(50_000, P) - 0.01) < 1e-9
    assert abs(price_shock_span(100_000, P) - 0.01) < 1e-9
    assert abs(price_shock_span(2_350_000, P) - 0.10) < 1e-6  # cap reached
    assert abs(price_shock_span(10_000_000, P) - 0.10) < 1e-9  # capped


def test_vol_spans_scale_and_cap() -> None:
    assert abs(vol_down_span(50_000, P) - 0.06) < 1e-9
    assert abs(vol_up_span(50_000, P) - 0.09) < 1e-9
    assert abs(vol_down_span(2_100_000, P) - 0.30) < 1e-6
    assert abs(vol_up_span(2_100_000, P) - 0.45) < 1e-6


def test_iv_shock_dte_adjustment() -> None:
    # span unchanged at 30 DTE; doc example: 45% span at 1 DTE -> ~124.8%
    assert abs(iv_shock(0.09, 30) - 0.09) < 1e-9
    assert abs(iv_shock(0.45, 1) - 0.45 * (30 ** 0.30)) < 1e-9
    assert abs(iv_shock(0.45, 1) - 1.2484) < 1e-3


def test_om_pct_scales_and_caps() -> None:
    assert abs(om_pct(100_000, P) - 0.005) < 1e-9
    assert abs(om_pct(200_000, P) - 0.005) < 1e-9
    assert om_pct(10_000_000, P) == P.om_cap  # capped at 2% for BTC


def test_scenarios_count_is_29() -> None:
    sc = scenarios(0.05)
    assert len(sc) == 29
    # 2 of them are extreme (weight 1/3)
    assert sum(1 for s in sc if abs(s.weight - 1 / 3) < 1e-9) == 2


def _short_call() -> MarginLeg:
    return MarginLeg("call", "short", qty=10, strike=63000, dte_days=7,
                     iv=0.40, contract_value=0.001, mark=470.0)


def test_maintenance_is_80pct_of_initial_when_no_ucf() -> None:
    r = compute_margin([_short_call()], spot=63000, params=P)
    assert r.initial_margin > 0
    assert abs(r.maintenance_margin - 0.8 * r.initial_margin) < 1e-6


def test_short_costs_more_than_long() -> None:
    short = compute_margin([_short_call()], spot=63000, params=P)
    long_leg = MarginLeg("call", "long", 10, 63000, 7, 0.40, 0.001, 470.0)
    long_ = compute_margin([long_leg], spot=63000, params=P)
    # long option risk is capped at premium; short has large stress loss
    assert short.initial_margin > long_.initial_margin


def test_long_option_margin_equals_premium() -> None:
    # Confirmed vs live Delta: a long option's margin == its premium (qty*cv*mark),
    # since max loss = premium paid and that dominates risk/floor.
    leg = MarginLeg("call", "long", qty=1, strike=63000, dte_days=10,
                    iv=0.55, contract_value=0.001, mark=2161.0)
    r = compute_margin([leg], spot=63215)
    assert abs(r.initial_margin - 1 * 0.001 * 2161.0) < 1e-6


def _iron_condor() -> list[MarginLeg]:
    cv, dte = 0.001, 10
    return [
        MarginLeg("call", "short", 1, 65000, dte, 0.47, cv, 1200.0),
        MarginLeg("call", "long", 1, 67000, dte, 0.45, cv, 600.0),
        MarginLeg("put", "short", 1, 61500, dte, 0.49, cv, 1250.0),
        MarginLeg("put", "long", 1, 59500, dte, 0.53, cv, 750.0),
    ]


def test_iron_condor_positive_finite() -> None:
    # 4-leg multi-expiry-capable structure computes a sane, positive margin.
    # (Net-credit exotics currently over-estimate vs Delta — conservative; the
    # live endpoint is exact. See ADR 0002.)
    legs = _iron_condor()
    r = compute_margin(legs, spot=63000)
    assert r.initial_margin > 0
    assert math.isfinite(r.initial_margin)


def test_defined_max_loss_finite_for_spread() -> None:
    # defined_max_loss is a UI risk utility: finite for a debit spread.
    legs = [
        MarginLeg("call", "long", 1, 63000, 10, 0.55, 0.001, 2160.0),
        MarginLeg("call", "short", 1, 65000, 10, 0.47, 0.001, 1200.0),
    ]
    ml = defined_max_loss(legs, spot=63000)
    assert 0.0 < ml < 5.0  # net debit, well-bounded


def test_spread_offsets_below_naked() -> None:
    naked = compute_margin([_short_call()], spot=63000, params=P)
    spread = compute_margin(
        [
            _short_call(),
            MarginLeg("call", "long", 10, 65000, 7, 0.38, 0.001, 250.0),
        ],
        spot=63000,
        params=P,
    )
    # buying the higher call caps the upside loss -> less margin than naked
    assert spread.initial_margin < naked.initial_margin
    assert math.isfinite(spread.risk_margin)
