# backend/tests/test_body_resolution.py
from app.sim.service import body_resolution_seconds


def test_resolution_boundaries() -> None:
    assert body_resolution_seconds(10 * 60) == 1          # <=15 min -> 1s
    assert body_resolution_seconds(15 * 60) == 1
    assert body_resolution_seconds(15 * 60 + 1) == 10     # >15 min -> 10s
    assert body_resolution_seconds(12 * 3600) == 10
    assert body_resolution_seconds(12 * 3600 + 1) == 60   # >12 h -> 1 min
    assert body_resolution_seconds(24 * 3600) == 60
    assert body_resolution_seconds(24 * 3600 + 1) == 300  # >24 h -> 5 min
    assert body_resolution_seconds(7 * 24 * 3600) == 300
