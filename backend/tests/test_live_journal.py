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
    assert round(s["pnl"], 4) == -0.14  # realised: (-0.5-0.02) + (0.4-0.02)
    assert round(s["fees"], 4) == 0.04
    assert s["maxMtm"] == 0.2 and s["minMtm"] == -0.3
    assert [lg["exit"] for lg in s["legs"]] == [150, 60]
