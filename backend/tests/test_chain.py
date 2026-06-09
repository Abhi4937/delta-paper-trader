"""Tests for option-chain normalization (real Delta ticker shape, no network)."""

from datetime import date

from app.services.chain import (
    build_chain,
    list_expiries,
    normalize_contract,
    parse_expiry_from_symbol,
)

# A real BTC put ticker captured from /v2/tickers (trimmed but shape-accurate).
PUT_SAMPLE = {
    "symbol": "P-BTC-95000-310726",
    "product_id": 133633,
    "contract_type": "put_options",
    "strike_price": "95000",
    "mark_price": "31627.40453254",
    "oi": "0.0340",
    "oi_value_usd": "2100",
    "contract_value": "0.001",
    "tick_size": "0.1",
    "spot_price": "63451.4",
    "quotes": {
        "best_bid": "30771",
        "best_ask": "31097",
        "bid_iv": "0.49",
        "ask_iv": "0.50",
        "mark_iv": "0.49153726",
        "bid_size": "8386",
        "ask_size": "5593",
    },
    "greeks": {
        "delta": "-0.98065623",
        "theta": "-5.29186557",
        "gamma": "0.00000397",
        "rho": "-135.48317688",
        "vega": "11.34855754",
    },
}

CALL_SAMPLE = {
    "symbol": "C-BTC-63000-310726",
    "product_id": 133600,
    "contract_type": "call_options",
    "strike_price": "63000",
    "mark_price": "1200.0",
    "oi": "5.2",
    "spot_price": "63451.4",
    "quotes": {"best_bid": "1195", "best_ask": "1205", "mark_iv": "0.41"},
    "greeks": {"delta": "0.52"},
}


def test_parse_expiry() -> None:
    assert parse_expiry_from_symbol("P-BTC-95000-310726") == date(2026, 7, 31)
    assert parse_expiry_from_symbol("C-ETH-3000-010126") == date(2026, 1, 1)
    assert parse_expiry_from_symbol("garbage") is None
    assert parse_expiry_from_symbol("P-BTC-95000-329926") is None  # bad month/day


def test_normalize_put() -> None:
    c = normalize_contract(PUT_SAMPLE)
    assert c is not None
    assert c.option_type == "put"
    assert c.strike == 95000
    assert c.quote.bid == 30771
    assert c.quote.ask == 31097
    assert c.quote.mark_iv is not None and abs(c.quote.mark_iv - 0.4915) < 1e-3
    assert c.greeks.delta is not None and c.greeks.delta < 0
    assert c.contract_value == 0.001
    assert c.expiry == date(2026, 7, 31)


def test_build_chain_groups_calls_and_puts() -> None:
    chain = build_chain([CALL_SAMPLE, PUT_SAMPLE], "BTC", date(2026, 7, 31))
    assert chain.underlying == "BTC"
    assert chain.spot == 63451.4
    # two different strikes -> two rows
    strikes = [r.strike for r in chain.rows]
    assert strikes == [63000, 95000]
    call_row = next(r for r in chain.rows if r.strike == 63000)
    assert call_row.call is not None and call_row.put is None
    # ATM is the strike nearest spot (63000)
    assert chain.atm_strike == 63000
    assert chain.atm_iv is not None and abs(chain.atm_iv - 0.41) < 1e-6


def test_list_expiries_unique_sorted() -> None:
    expiries = list_expiries([CALL_SAMPLE, PUT_SAMPLE])
    assert expiries == [date(2026, 7, 31)]
