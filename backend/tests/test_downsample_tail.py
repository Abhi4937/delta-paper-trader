"""Unit tests for the uniform-resolution chart helpers in sim.service.

The 1s live tail must roll into the body's resolution so an old position's chart is one
uniform resolution (no dominating 1s tail). Buckets are epoch-aligned to match the body's
Timescale time_bucket grid.
"""
from datetime import UTC, datetime, timedelta

from app.sim.service import _downsample_tail, body_resolution_seconds, position_resolution_seconds


def _s(t_ms: int, pnl: float) -> dict:
    # one 1s tail sample is a degenerate candle (open=high=low=close=pnl)
    return {
        "t": t_ms, "pnl": pnl, "pnlOpen": pnl, "pnlHigh": pnl, "pnlLow": pnl,
        "delta": 0.0, "theta": 0.0, "vega": 0.0, "atmIv": {}, "legs": {},
    }


def _ohlc(c: dict) -> tuple:
    return (c["pnlOpen"], c["pnlHigh"], c["pnlLow"], c["pnl"])


def test_downsample_tail_buckets_to_resolution_with_true_ohlc():
    # six 1s samples spanning two 5-min buckets; values chosen so high/low are interior
    base = 300_000  # 300s, aligned to a 5-min (300s) bucket start
    tail = [
        _s(base + 0, 10),
        _s(base + 60_000, 25),   # high of bucket 1
        _s(base + 120_000, 5),   # low of bucket 1
        _s(base + 240_000, 18),  # close of bucket 1
        _s(base + 300_000, 20),  # bucket 2 opens (600s)
        _s(base + 360_000, 22),  # close of bucket 2
    ]
    out = _downsample_tail(tail, 300)
    assert len(out) == 2
    # bucket 1: open=first, high=max, low=min, close=last-in-bucket, anchored to 300s
    assert out[0]["t"] == 300_000
    assert _ohlc(out[0]) == (10, 25, 5, 18)
    # bucket 2 anchored to 600s
    assert out[1]["t"] == 600_000
    assert _ohlc(out[1]) == (20, 22, 20, 22)


def test_downsample_tail_passthrough_for_1s():
    tail = [_s(1000, 1), _s(2000, 2)]
    assert _downsample_tail(tail, 1) == tail  # fresh positions keep 1s
    assert _downsample_tail([], 300) == []


class _Pos:
    def __init__(self, opened_at, status="open", closed_at=None):
        self.opened_at = opened_at
        self.status = status
        self.closed_at = closed_at


def test_body_resolution_tiers():
    assert body_resolution_seconds(10 * 60) == 1       # ≤15m → 1s
    assert body_resolution_seconds(6 * 3600) == 10     # ≤12h → 10s
    assert body_resolution_seconds(20 * 3600) == 60    # ≤24h → 1m
    assert body_resolution_seconds(48 * 3600) == 300   # >24h → 5m


def test_position_resolution_uses_age_for_open_and_lifetime_for_closed():
    now = datetime.now(UTC)
    # open position ~2 days old → 5m
    assert position_resolution_seconds(_Pos(now - timedelta(hours=48))) == 300
    # closed position with a ~10-minute lifetime → 1s (uniform, no live tail)
    opened = now - timedelta(hours=5)
    assert position_resolution_seconds(
        _Pos(opened, status="closed", closed_at=opened + timedelta(minutes=10))
    ) == 1
