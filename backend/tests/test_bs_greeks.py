"""Black-Scholes greeks (local fallback for when Delta's feed greeks gap).

Checked against hand-computed textbook values for an ATM option
(S=K=100, T=1y, sigma=20%, r=0): d1=0.1, N'(d1)=0.396953.
"""

from app.engines.margin import bs_greeks


def test_bs_greeks_atm_textbook() -> None:
    g = bs_greeks("call", 100.0, 100.0, 1.0, 0.20, 0.0)
    assert abs(g["delta"] - 0.539828) < 1e-4
    assert abs(g["gamma"] - 0.019848) < 1e-4
    assert abs(g["vega"] - 0.396953) < 1e-4  # per 1% vol
    assert abs(g["theta"] - (-0.010876)) < 1e-4  # per calendar day


def test_bs_greeks_put_relations() -> None:
    c = bs_greeks("call", 100.0, 100.0, 1.0, 0.20, 0.0)
    p = bs_greeks("put", 100.0, 100.0, 1.0, 0.20, 0.0)
    assert abs(p["delta"] - (c["delta"] - 1.0)) < 1e-9  # put delta = call delta - 1
    assert abs(p["vega"] - c["vega"]) < 1e-9  # vega identical call/put
    assert abs(p["gamma"] - c["gamma"]) < 1e-9  # gamma identical call/put


def test_bs_greeks_degenerate_returns_zeros() -> None:
    assert bs_greeks("call", 100.0, 100.0, 0.0, 0.20) == {
        "delta": 0.0,
        "gamma": 0.0,
        "theta": 0.0,
        "vega": 0.0,
    }
    assert bs_greeks("put", 100.0, 100.0, 1.0, 0.0) == {
        "delta": 0.0,
        "gamma": 0.0,
        "theta": 0.0,
        "vega": 0.0,
    }
