"""Contract test for service.bounded_series — the GET /api/state series bound.

Bug it locks: a position open longer than the 1s tail used to ship its whole in-memory
ring; the client (count-capped) then trimmed the entry-era rows, so the chart no longer
started at entry. The fix bounds the 1s portion to TAIL_1S and serves everything before
it from the durable 10s DB history. These tests prove the merge keeps the position's
start at entry with a bounded payload no matter how long the ring grows.
"""

from app.sim import service
from app.sim.service import TAIL_1S


def _rows(start_t: int, count: int, step: int) -> list[dict[str, int]]:
    """count rows at `step`-second spacing (epoch-ms t), pnl tagged = t for identity."""
    return [{"t": (start_t + i * step) * 1000, "pnl": (start_t + i * step) * 1000} for i in range(count)]


def test_no_ring_returns_full_db_history() -> None:
    # Before any live tick, the chart is the full-life 10s history, untouched.
    db = _rows(0, 100, 10)
    assert service.bounded_series(db, []) == db


def test_short_ring_appends_after_db_no_overlap() -> None:
    # Ring shorter than the tail cap: keep DB rows strictly before the ring start,
    # then the whole ring — no duplicated timestamps at the seam.
    db = _rows(0, 360, 10)  # 0..3590s at 10s (full life so far)
    ring = _rows(3540, 60, 1)  # last 60s at 1s, overlapping the tail of db
    out = service.bounded_series(db, ring)
    first_ring_t = ring[0]["t"]
    assert out == [s for s in db if s["t"] < first_ring_t] + ring
    # strictly increasing, no dup at the seam
    ts = [s["t"] for s in out]
    assert ts == sorted(ts) and len(ts) == len(set(ts))
    # starts at entry (t=0)
    assert out[0]["t"] == 0


def test_ring_longer_than_tail_keeps_entry_history() -> None:
    # THE bug case: ring far exceeds TAIL_1S (a long-open position). The 1s portion is
    # trimmed to the last TAIL_1S, and the 10s DB history fills everything before it —
    # so the series still STARTS AT ENTRY rather than at "tail-ago".
    life_s = TAIL_1S * 3  # open 3x the tail window
    db = _rows(0, life_s // 10, 10)  # full-life 10s history from entry
    ring = _rows(0, life_s, 1)  # 1s ring spanning the whole life (oversized)
    out = service.bounded_series(db, ring)

    # tail is bounded to exactly TAIL_1S samples
    tail = out[-TAIL_1S:]
    assert len(tail) == TAIL_1S
    # the bounded payload still begins at entry (t=0), NOT at tail-ago
    assert out[0]["t"] == 0
    # the seam: last 10s row sits strictly before the first 1s tail row
    tail_start = (life_s - TAIL_1S) * 1000
    assert tail[0]["t"] == tail_start
    head = [s for s in out if s["t"] < tail_start]
    assert head and all(s["t"] % 10000 == 0 for s in head)  # head is the 10s grid
    assert head[0]["t"] == 0 and head[-1]["t"] < tail_start
