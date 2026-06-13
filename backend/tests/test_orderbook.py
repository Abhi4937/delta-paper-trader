"""/api/orderbook: settled/delisted symbols return an empty book, not a 500.

Delta returns 400 for a contract that has settled; the endpoint must treat that as
"no book" (the contract is gone) instead of surfacing an Internal Server Error.
"""

import httpx
import pytest

from app.main import _orderbook


class _FakeDelta:
    """Stand-in for DeltaRestClient: either raise an HTTP status or return a result."""

    def __init__(self, *, status: int | None = None, result: object = None) -> None:
        self._status = status
        self._result = result

    async def get(self, path: str) -> object:
        if self._status is not None:
            req = httpx.Request("GET", "https://api.india.delta.exchange" + path)
            raise httpx.HTTPStatusError(
                "err", request=req, response=httpx.Response(self._status, request=req)
            )
        return {"result": self._result}


async def test_orderbook_settled_returns_empty_not_500() -> None:
    out = await _orderbook(_FakeDelta(status=400), "C-BTC-60000-120626")
    assert out == {
        "symbol": "C-BTC-60000-120626",
        "buy": [],
        "sell": [],
        "available": False,
    }


async def test_orderbook_live_returns_book() -> None:
    out = await _orderbook(
        _FakeDelta(result={"buy": [{"price": "1"}], "sell": []}), "C-BTC-60000-310726"
    )
    assert out["available"] is True
    assert out["buy"] == [{"price": "1"}]
    assert out["sell"] == []


async def test_orderbook_non_400_error_still_raises() -> None:
    # a 500/timeout is a real error — don't swallow it as "settled"
    with pytest.raises(httpx.HTTPStatusError):
        await _orderbook(_FakeDelta(status=500), "C-BTC-60000-310726")
