"""Expiry settlement (1D): detect expired legs + map Delta settlement prices.
The settle/close + balance/ledger path is integration-validated against the dev DB."""

from datetime import UTC, datetime
from types import SimpleNamespace

from app.sim.service import fetch_settlement_prices, leg_is_expired


def _leg(expiry: str) -> SimpleNamespace:
    return SimpleNamespace(expiry=expiry, status="open")


def test_leg_is_expired_after_settlement() -> None:
    now = datetime(2026, 6, 14, 12, 1, tzinfo=UTC)  # 17:31 IST on the 14th
    assert leg_is_expired(_leg("2026-06-14"), now) is True
    assert leg_is_expired(_leg("2026-06-13"), now) is True


def test_leg_not_expired_before_settlement() -> None:
    now = datetime(2026, 6, 14, 11, 59, tzinfo=UTC)
    assert leg_is_expired(_leg("2026-06-14"), now) is False  # not yet settled
    assert leg_is_expired(_leg("2026-06-15"), now) is False


def test_leg_is_expired_bad_date() -> None:
    assert leg_is_expired(_leg("garbage"), datetime(2026, 6, 14, 12, 1, tzinfo=UTC)) is False


class _FakeClient:
    def __init__(self, result: list[dict]) -> None:
        self._result = result

    async def get(self, path: str, params: dict | None = None) -> dict:
        return {"result": self._result}


async def test_fetch_settlement_prices_maps_and_filters() -> None:
    out = await fetch_settlement_prices(
        _FakeClient(
            [
                {"symbol": "C-BTC-65800-130626", "settlement_price": 0},  # OTM call → 0
                {"symbol": "P-BTC-65800-130626", "settlement_price": 1878.83},  # ITM put
                {"symbol": "C-BTC-99000-130626", "settlement_price": None},  # not published → skip
                {"symbol": None, "settlement_price": 5},  # no symbol → skip
            ]
        )
    )
    assert out == {"C-BTC-65800-130626": 0.0, "P-BTC-65800-130626": 1878.83}
