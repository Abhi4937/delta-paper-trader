"""Contract test for service.build_sample — the per-second MTM sample that feeds the
position charts. The lightweight-ring regression shipped samples with empty legs/atmIv,
collapsing the per-leg + IV chart lines. This locks the contract: every open leg gets a
full row and every expiry gets an ATM-IV entry.
"""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

from app.sim import service
from app.sim.marketview import Quote


class FakeMV:
    def quote(self, symbol: str) -> Quote:
        return Quote(mark=120.0, bid=118.0, ask=122.0, iv=0.45, delta=0.5, gamma=0.0, theta=-1.2, vega=3.4)

    def atm_iv(self, underlying: str, expiry: str) -> float:
        return 0.50

    def spot(self, underlying: str) -> float:
        return 60000.0


def _leg(side: str, expiry: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        symbol=f"C-BTC-60000-{expiry}",
        side=side,
        qty=1,
        contract_value=0.001,
        entry=100.0,
        expiry=expiry,
        status="open",
    )


def test_build_sample_carries_full_leg_and_iv_detail() -> None:
    legs = [_leg("sell", "2026-06-13"), _leg("buy", "2026-06-20")]
    pos = SimpleNamespace(underlying="BTC", legs=legs)
    s = service.build_sample(pos, FakeMV(), datetime(2026, 6, 11, tzinfo=UTC))

    # one full leg row per open leg (NOT empty — that was the bug)
    assert set(s["legs"].keys()) == {str(lg.id) for lg in legs}
    for row in s["legs"].values():
        assert set(row.keys()) == {"pnl", "iv", "delta", "bid", "ask"}
        assert row["iv"] == 0.45
        assert row["bid"] == 118.0 and row["ask"] == 122.0

    # one ATM-IV entry per distinct expiry (NOT empty)
    assert set(s["atmIv"].keys()) == {"2026-06-13", "2026-06-20"}
    assert all(v == 0.50 for v in s["atmIv"].values())

    # net aggregates present
    for k in ("t", "pnl", "delta", "theta", "vega"):
        assert k in s


def test_build_sample_skips_closed_legs() -> None:
    legs = [_leg("sell", "2026-06-13"), _leg("buy", "2026-06-13")]
    legs[1].status = "closed"
    pos = SimpleNamespace(underlying="BTC", legs=legs)
    s = service.build_sample(pos, FakeMV(), datetime(2026, 6, 11, tzinfo=UTC))
    assert set(s["legs"].keys()) == {str(legs[0].id)}
