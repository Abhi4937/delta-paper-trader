"""TDD for the slippage / paper-fill engine (ADR 0003).

Orderbook-walk VWAP + trading-band cap (IOC partial fill) + thin-book fallback.
All money is Decimal — floats never touch a fill price.
"""

from decimal import Decimal as D

from app.engines.slippage import Level, OrderBook, compute_fill


def _book(bids, asks) -> OrderBook:
    return OrderBook(
        bids=[Level(D(str(p)), D(str(s))) for p, s in bids],
        asks=[Level(D(str(p)), D(str(s))) for p, s in asks],
    )


def test_buy_walks_asks_vwap() -> None:
    book = _book([], [(100, 5), (101, 10)])
    f = compute_fill("buy", D(8), book, mark=D(100), tick_size=D("0.5"))
    # 5@100 + 3@101 = 803 / 8 = 100.375
    assert f.filled_qty == D(8)
    assert f.vwap == D("100.375")
    assert f.fill_price == D("100.375")  # no impact (k=0)
    assert not f.used_fallback


def test_sell_walks_bids_vwap() -> None:
    book = _book([(99, 5), (98, 10)], [])
    f = compute_fill("sell", D(8), book, mark=D(99), tick_size=D("0.5"))
    # 5@99 + 3@98 = 789 / 8 = 98.625
    assert f.vwap == D("98.625")


def test_partial_fill_when_depth_short() -> None:
    book = _book([], [(100, 5)])
    f = compute_fill("buy", D(10), book, mark=D(100), tick_size=D("0.5"))
    assert f.filled_qty == D(5)  # IOC: only 5 available
    assert f.vwap == D(100)


def test_band_cap_excludes_far_levels() -> None:
    # band 105: the 110 ask is outside the band -> excluded -> partial fill.
    book = _book([], [(100, 5), (110, 10)])
    f = compute_fill("buy", D(8), book, mark=D(100), tick_size=D("0.5"), band=D(105))
    assert f.filled_qty == D(5)
    assert f.band_capped


def test_empty_book_fallback() -> None:
    book = _book([], [])
    f = compute_fill("buy", D(3), book, mark=D(100), tick_size=D("0.5"),
                     slippage_ticks=D(2))
    assert f.used_fallback
    assert f.filled_qty == D(3)
    assert f.fill_price == D(101)  # mark + 2 ticks * 0.5


def test_impact_pushes_buy_above_vwap() -> None:
    book = _book([], [(100, 1000)])
    f = compute_fill("buy", D(1), book, mark=D(100), tick_size=D("0.5"),
                     contract_value=D(1), k=D("0.1"), vol_24h=None,
                     illiquid_floor=D("0.01"))
    # impact modelled (k>0) + vol unknown -> fraction = floor 0.01 -> impact = 1
    assert f.vwap == D(100)
    assert f.fill_price == D(101)
    assert f.impact == D(1)


def test_slippage_vs_mark_bps_signed() -> None:
    book = _book([], [(100, 5), (101, 10)])
    f = compute_fill("buy", D(8), book, mark=D(100), tick_size=D("0.5"))
    # (100.375 - 100)/100 * 1e4 = 37.5 bps
    assert abs(f.slippage_vs_mark_bps - D("37.5")) < D("0.001")
