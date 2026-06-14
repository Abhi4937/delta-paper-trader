# backend/tests/test_mtm_ohlc.py
from app.sim.service import MtmOhlc


def test_ohlc_captures_spike_between_endpoints() -> None:
    acc = MtmOhlc()
    for v in [10.0, 25.0, -5.0, 12.0]:  # spikes up then down, ends mid
        acc.add(v)
    assert acc.started() is True
    assert acc.snapshot() == {"open": 10.0, "high": 25.0, "low": -5.0, "close": 12.0}


def test_ohlc_reset_clears_state() -> None:
    acc = MtmOhlc()
    acc.add(3.0)
    acc.reset()
    assert acc.started() is False
    acc.add(7.0)
    assert acc.snapshot() == {"open": 7.0, "high": 7.0, "low": 7.0, "close": 7.0}
