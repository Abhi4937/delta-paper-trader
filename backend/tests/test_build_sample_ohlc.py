# backend/tests/test_build_sample_ohlc.py
# Tail/live samples are single points → OHLC must be degenerate (all == pnl).
def test_sample_dict_keys_present() -> None:
    sample = {"t": 1, "pnl": 4.0, "pnlOpen": 4.0, "pnlHigh": 4.0, "pnlLow": 4.0}
    assert sample["pnlOpen"] == sample["pnlHigh"] == sample["pnlLow"] == sample["pnl"]
