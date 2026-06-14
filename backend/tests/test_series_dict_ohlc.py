# backend/tests/test_series_dict_ohlc.py
from datetime import UTC, datetime
from types import SimpleNamespace

from app.sim.service import series_dict


def test_series_dict_includes_ohlc() -> None:
    row = SimpleNamespace(
        time=datetime(2026, 6, 14, tzinfo=UTC),
        pnl=12.0, pnl_open=10.0, pnl_high=25.0, pnl_low=-5.0,
        delta=0.1, theta=-2.0, vega=3.0, atm_iv={"2026-06-20": 0.5}, legs={},
    )
    out = series_dict(row)
    assert out["pnl"] == 12.0
    assert (out["pnlOpen"], out["pnlHigh"], out["pnlLow"]) == (10.0, 25.0, -5.0)


def test_series_dict_ohlc_falls_back_to_pnl_when_null() -> None:
    row = SimpleNamespace(
        time=datetime(2026, 6, 14, tzinfo=UTC),
        pnl=8.0, pnl_open=None, pnl_high=None, pnl_low=None,
        delta=0.0, theta=0.0, vega=0.0, atm_iv={}, legs={},
    )
    out = series_dict(row)
    assert out["pnlOpen"] == out["pnlHigh"] == out["pnlLow"] == 8.0
