import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.live.journal import mtm_stats, summarize

T0 = 1_790_000_000_000


def test_empty_series():
    s = mtm_stats([])
    assert s["maxMtm"] is None and s["maxDrawdown"] == 0.0


def test_max_min_and_drawdown_with_times():
    bars = [  # (t, open, high, low, close)
        (T0, 0, 5, -1, 4),
        (T0 + 10_000, 4, 12, 3, 10),  # peak 12
        (T0 + 20_000, 10, 11, -6, -5),  # trough -6 -> DD 18 from the peak of 12
        (T0 + 30_000, -5, 2, -8, 1),  # new min -8, but DD from 12 is 20
    ]
    s = mtm_stats(bars)
    assert (s["maxMtm"], s["maxMtmAt"]) == (12, T0 + 10_000)
    assert (s["minMtm"], s["minMtmAt"]) == (-8, T0 + 30_000)
    assert s["maxDrawdown"] == 20
    assert (s["drawdownPeakAt"], s["drawdownTroughAt"]) == (T0 + 10_000, T0 + 30_000)


def test_a_bars_own_high_is_not_its_own_drawdown_peak():
    # one bar that went 0 -> +10 -> -2: order unknown, so no drawdown from its own high
    assert mtm_stats([(T0, 0, 10, -2, 1)])["maxDrawdown"] == 2


def test_closed_trade_summary_snapshot():
    opened = datetime(2026, 9, 23, 10, 0, tzinfo=UTC)
    legs = [
        SimpleNamespace(
            symbol="C-BTC-1",
            side="sell",
            qty=10,
            type="call",
            strike=1,
            expiry="2026-09-26",
            entry=100,
            exit_price=150,
            exit_at=opened + timedelta(minutes=30),
            exit_reason="SL exit",
            exit_gross=-0.5,
            exit_fees=0.02,
            stop_pnl=-0.4,
            sl_price=140,
            tp_price=None,
            stop_price=140,
            status="closed",
            contract_value=0.001,
            entry_at=opened,
            entry_fees=0.01,
            entry_margin=5.0,
            last_margin=6.0,
            mark_at_entry=99,
            mark_at_exit=148,
        ),
        SimpleNamespace(
            symbol="P-BTC-1",
            side="sell",
            qty=10,
            type="put",
            strike=1,
            expiry="2026-09-26",
            entry=100,
            exit_price=60,
            exit_at=opened + timedelta(minutes=30),
            exit_reason="SL exit",
            exit_gross=0.4,
            exit_fees=0.02,
            stop_pnl=None,
            sl_price=None,
            tp_price=None,
            stop_price=None,
            status="closed",
            contract_value=0.001,
            entry_at=opened,
            entry_fees=0.01,
            entry_margin=5.0,
            last_margin=4.0,
            mark_at_entry=101,
            mark_at_exit=61,
        ),
    ]
    pos = SimpleNamespace(
        id=uuid.uuid4(),
        name="BTC 2026-09-26 (live)",
        underlying="BTC",
        expiry="2026-09-26",
        status="closed",
        opened_at=opened,
        closed_at=opened + timedelta(minutes=30),
        close_reason="SL: leg TP/SL",
        auto_exit=True,
        stop_loss_amount=1.0,
        stop_loss_pct_of_margin=None,
        target_pnl=None,
        target_pct_of_margin=None,
        legs=legs,
    )
    s = summarize(pos, [(T0, 0, 0.2, -0.3, -0.14)], datetime.now(UTC))
    assert s["durationSeconds"] == 1800
    # net: gross (-0.5 + 0.4) minus entry (0.01+0.01) AND exit (0.02+0.02) brokerage
    assert round(s["pnl"], 4) == -0.16
    assert round(s["fees"], 4) == 0.06
    call = s["legs"][0]
    assert (call["entryFees"], call["entryMargin"], call["exitMargin"]) == (0.01, 5.0, 6.0)
    # sold at 100 vs mark 99: +0.01/unit better than mark => negative (favourable) slippage
    assert round(call["entrySlippage"], 4) == -0.01
    # bought back at 150 vs mark 148: paid 2/unit x 10 x 0.001 = 0.02
    assert round(call["exitSlippage"], 4) == 0.02
    assert s["maxMtm"] == 0.2 and s["minMtm"] == -0.3
    assert [lg["exit"] for lg in s["legs"]] == [150, 60]


def test_fill_run_stops_at_the_other_side_and_summarizes():
    from app.live.journal import fill_run, fill_summary

    fills = [  # newest first
        {"product_id": 7, "side": "buy", "size": 4, "price": "150", "commission": "0.01",
         "created_at": "2026-09-24T10:05:00Z", "order_id": 11},
        {"product_id": 9, "side": "sell", "size": 1, "price": "1", "commission": "0"},
        {"product_id": 7, "side": "buy", "size": 6, "price": "160", "commission": "0.02",
         "created_at": 1790244000000000, "order_id": 12},
        {"product_id": 7, "side": "sell", "size": 10, "price": "100", "commission": "0.03"},
    ]
    run = fill_run(fills, 7, "buy")
    assert [f["order_id"] for f in run] == [11, 12]  # stops at the opening sell
    got = fill_summary(run)
    assert got["size"] == 10 and round(got["price"], 4) == 156.0  # (4*150 + 6*160) / 10
    assert round(got["fees"], 4) == 0.03
    assert got["first_at"] < got["last_at"]
    assert fill_summary([]) is None


def test_candles_carry_leg_pnl_with_high_low_flipped_for_sold_legs():
    from app.live.journal import candle_rows

    c = [[1, 100, 150, 80, 120]]  # t, o, h, l, c (premium)
    (_, *_, po, ph, pl, pc), = candle_rows("sell", 100, 10, 0.001, c)
    assert (round(po, 3), round(ph, 3), round(pl, 3), round(pc, 3)) == (0.0, 0.2, -0.5, -0.2)
    (_, *_, po, ph, pl, pc), = candle_rows("buy", 100, 10, 0.001, c)
    assert (round(ph, 3), round(pl, 3)) == (0.5, -0.2)
