"""3A wiring: build_sample's per-leg greeks come from the feed when present, else a
local Black-Scholes fallback (so panels don't read zeros when the feed omits greeks)."""

from types import SimpleNamespace

from app.sim.marketview import Quote
from app.sim.service import leg_greeks


def _q(
    present: bool, *, iv: float = 0.5, delta: float = 0.0, theta: float = 0.0, vega: float = 0.0
) -> Quote:
    return Quote(
        mark=100, bid=99, ask=101, iv=iv, delta=delta, gamma=0.0, theta=theta, vega=vega,
        greeks_present=present,
    )


def _leg(strike: float = 64000.0, dte: float = 7.0, kind: str = "call") -> SimpleNamespace:
    return SimpleNamespace(type=kind, strike=strike, dte=dte)


def test_uses_feed_greeks_when_present() -> None:
    d, t, v = leg_greeks(_leg(), _q(True, delta=0.42, theta=-0.5, vega=1.5), spot=64000.0)
    assert (d, t, v) == (0.42, -0.5, 1.5)


def test_bs_fallback_when_feed_omits_greeks() -> None:
    # greeks absent (0/false) but iv+spot+dte present → compute locally, not the feed zero
    d, t, v = leg_greeks(_leg(strike=64000, dte=7), _q(False, iv=0.5), spot=64000.0)
    assert d != 0.0
    assert 0.3 < d < 0.7  # ATM-ish call delta ~0.5
    assert v > 0.0  # vega positive


def test_none_quote_is_zeros() -> None:
    assert leg_greeks(_leg(), None, spot=64000.0) == (0.0, 0.0, 0.0)


def test_no_fallback_without_iv_returns_feed() -> None:
    # can't compute (no iv) → return the feed value (zero), no crash
    assert leg_greeks(_leg(), _q(False, iv=0.0), spot=64000.0) == (0.0, 0.0, 0.0)
