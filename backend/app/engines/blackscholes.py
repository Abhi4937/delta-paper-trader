"""Black-Scholes price + greeks (r = 0 for crypto), exact via math.erf.

Self-contained (no import of margin.py, which imports payoff.py) so the Analyse
Payoff engine can reuse it without a circular import. Conventions match the
frontend `lib/bs.ts`: vega per 1 vol-point, theta per day.
"""

from __future__ import annotations

import math


def _ncdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _npdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def bs_price(option_type: str, S: float, K: float, T: float, sigma: float) -> float:
    """Black-Scholes price; intrinsic when T<=0 or sigma<=0 (the expiry payoff)."""
    if T <= 0 or sigma <= 0 or S <= 0:
        return max(S - K, 0.0) if option_type == "call" else max(K - S, 0.0)
    sq = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + 0.5 * sigma * sigma * T) / sq
    d2 = d1 - sq
    if option_type == "call":
        return S * _ncdf(d1) - K * _ncdf(d2)
    return K * _ncdf(-d2) - S * _ncdf(-d1)


def greeks(option_type: str, S: float, K: float, T: float, sigma: float) -> dict[str, float]:
    """Per-option greeks. delta (dimensionless), gamma, vega (per 1 vol-pt), theta (per day)."""
    if T <= 0 or sigma <= 0 or S <= 0:
        return {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0}
    sq = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + 0.5 * sigma * sigma * T) / sq
    delta = _ncdf(d1) if option_type == "call" else _ncdf(d1) - 1.0
    gamma = _npdf(d1) / (S * sq)
    vega = S * _npdf(d1) * math.sqrt(T) / 100.0  # per 1 vol-point
    theta = -(S * _npdf(d1) * sigma) / (2.0 * math.sqrt(T)) / 365.0  # per day
    return {"delta": delta, "gamma": gamma, "theta": theta, "vega": vega}
