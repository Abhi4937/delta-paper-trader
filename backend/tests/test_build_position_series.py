# backend/tests/test_build_position_series.py
from app.sim.service import _assemble_series


def test_assemble_open_uses_body_before_tail_and_1s_tail() -> None:
    # body = coarse points (older), ring = 1s tail (recent). Seam at ring[0].t.
    body = [{"t": 1000, "pnl": 1.0}, {"t": 2000, "pnl": 2.0}, {"t": 9000, "pnl": 9.0}]
    ring = [{"t": 5000, "pnl": 5.0}, {"t": 6000, "pnl": 6.0}]  # tail begins at 5000
    out = _assemble_series(body, ring)
    # body kept only strictly before the tail start; then the full tail
    assert [p["t"] for p in out] == [1000, 2000, 5000, 6000]


def test_assemble_empty_ring_returns_body() -> None:
    body = [{"t": 1000, "pnl": 1.0}]
    assert _assemble_series(body, []) == body
