import json

import httpx
import pytest

from app.config import Settings
from app.live.client import LiveClient, OrderRefused
from app.live.executor import ExitTarget, exit_group


class FakeDelta:
    """Positions + an order log. IOC fills only when the limit is within `fill_band` of 100."""

    def __init__(self, sizes, fill_band=0.10):
        self.sizes = dict(sizes)
        self.fill_band = fill_band
        self.orders: list[dict] = []
        self.cancels: list[dict] = []

    def handler(self, req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content) if req.content else None
        if req.method == "GET" and req.url.path == "/v2/positions/margined":
            res = [{"product_id": p, "size": s} for p, s in self.sizes.items() if s]
            return httpx.Response(200, json={"success": True, "result": res})
        if req.method == "POST" and req.url.path == "/v2/orders":
            self.orders.append(body)
            pid, size = body["product_id"], body["size"]
            fills = body["order_type"] == "market_order" or (
                abs(float(body["limit_price"]) - 100) / 100 >= self.fill_band - 1e-9
            )
            if fills and "stop_order_type" not in body:
                cur = self.sizes[pid]
                self.sizes[pid] = cur + size if body["side"] == "buy" else cur - size
            return httpx.Response(200, json={"success": True, "result": {"id": len(self.orders)}})
        if req.method == "DELETE" and req.url.path == "/v2/orders":
            self.cancels.append(body)
            return httpx.Response(200, json={"success": True, "result": {}})
        return httpx.Response(404, json={"success": False, "error": {"code": "not_found"}})


def client(fake, enabled=True):
    s = Settings(live_trading_enabled=enabled, delta_api_base="https://delta.test")
    return LiveClient("k", "s", s, transport=httpx.MockTransport(fake.handler))


async def _noop(level, msg):
    pass


async def test_place_refuses_without_reduce_only():
    fake = FakeDelta({})
    c = client(fake)
    with pytest.raises(OrderRefused):
        await c._place({"product_id": 1, "size": 1, "side": "buy", "order_type": "market_order"})
    assert fake.orders == []


async def test_kill_switch_blocks_every_order():
    fake = FakeDelta({1: -5})
    c = client(fake, enabled=False)
    with pytest.raises(OrderRefused):
        await c.close_market(1, "buy", 5)
    with pytest.raises(OrderRefused):
        await c.place_stop(1, "buy", 5, 150)
    assert fake.orders == []


async def test_stop_is_reduce_only_stop_market_on_mark():
    fake = FakeDelta({1: -5})
    await client(fake).place_stop(1, "buy", 5, 150.5)
    o = fake.orders[0]
    assert o["reduce_only"] is True
    assert o["stop_order_type"] == "stop_loss_order"
    assert o["stop_trigger_method"] == "mark_price"
    assert o["order_type"] == "market_order"
    assert o["stop_price"] == "150.5"


async def test_exit_closes_shorts_first_widens_band_and_cancels_stops():
    fake = FakeDelta({1: 10, 2: -10})  # 1 = long, 2 = short
    targets = [ExitTarget(1, "L", 0.5, 11), ExitTarget(2, "S", 0.5, 22)]
    res = await exit_group(client(fake), targets, lambda s: 100.0, _noop, pause=0)
    assert res.flat
    assert all(o["reduce_only"] is True for o in fake.orders)
    assert fake.orders[0]["product_id"] == 2 and fake.orders[0]["side"] == "buy"
    assert [o["limit_price"] for o in fake.orders if o["product_id"] == 2] == ["105", "110"]
    assert [o["limit_price"] for o in fake.orders if o["product_id"] == 1] == ["95", "90"]
    assert fake.sizes == {1: 0, 2: 0}
    assert {c["id"] for c in fake.cancels} == {11, 22}


async def test_exit_falls_back_to_market_when_no_band_fills():
    fake = FakeDelta({2: -10}, fill_band=9.0)  # no IOC ever fills
    res = await exit_group(
        client(fake), [ExitTarget(2, "S", 0.5, None)], lambda s: 100.0, _noop, pause=0
    )
    assert res.flat
    kinds = [o["order_type"] for o in fake.orders]
    assert kinds == ["limit_order"] * 4 + ["market_order"]


async def test_emergency_goes_straight_to_market():
    fake = FakeDelta({2: -10})
    res = await exit_group(
        client(fake),
        [ExitTarget(2, "S", 0.5, None)],
        lambda s: 100.0,
        _noop,
        emergency=True,
        pause=0,
    )
    assert res.flat
    assert [o["order_type"] for o in fake.orders] == ["market_order"]


async def test_exit_stops_when_kill_switch_off():
    fake = FakeDelta({2: -10})
    res = await exit_group(
        client(fake, enabled=False),
        [ExitTarget(2, "S", 0.5, None)],
        lambda s: 100.0,
        _noop,
        pause=0,
    )
    assert not res.flat and res.refused
    assert fake.orders == []


def test_delta_errors_are_explained():
    from app.live.client import DeltaError

    def err(code):
        return DeltaError(401, {"success": False, "error": {"code": code}})

    assert "whitelist" in err("ip_not_whitelisted_for_api_key").explain()
    assert "key is wrong" in err("InvalidApiKey").explain()
    assert "secret" in err("Signature Mismatch").explain()
    assert "clock" in err("SignatureExpired").explain()
    assert "Trading" in err("UnauthorizedApiAccess").explain()


def test_telegram_reasons_are_explained():
    from app.live.alerts import explain_telegram

    assert "token" in explain_telegram("Unauthorized")
    assert "chat id" in explain_telegram("Bad Request: chat not found")
    assert "Start" in explain_telegram("Forbidden: bot can't initiate conversation with a user")


def test_usd_wallet_prefers_available_balance():
    from app.live.sync import usd_wallet

    w = usd_wallet([{"asset_symbol": "USD", "balance": "500", "available_balance": "320.5"}])
    assert w == {"balance": 500.0, "available": 320.5}
    assert usd_wallet([{"asset_symbol": "USD", "balance": "500"}]) == {
        "balance": 500.0,
        "available": 500.0,
    }
