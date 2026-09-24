"""Per-user signed Delta client — the ONLY code that may send an order to Delta.

Ring-fence: built from the user's vault trade key, never from the global read-only env
key, and never imported by the paper-sim or market-data paths. Every order goes through
`_place`, which refuses anything that is not `reduce_only` (it can only shrink an existing
position — never open, add to, or flip one) and refuses everything while the global kill
switch (LIVE_TRADING_ENABLED) is off. Each attempt and its outcome is returned for the
caller to journal.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, Literal
from urllib.parse import urlencode

import httpx

from app.config import Settings, get_settings
from app.delta.rest import USER_AGENT, sign

Side = Literal["buy", "sell"]


class OrderRefused(Exception):
    """Refused locally — never sent to Delta."""


class DeltaError(Exception):
    def __init__(self, status: int, body: Any) -> None:
        super().__init__(f"Delta {status}: {body}")
        self.status = status
        self.body = body

    @property
    def code(self) -> str:
        err = self.body.get("error") if isinstance(self.body, dict) else None
        return str(err.get("code", "")) if isinstance(err, dict) else ""

    def explain(self) -> str:
        """Delta's error code plus what to do about it."""
        c = self.code or str(self.status)
        low = c.lower()
        if "ip_not_whitelisted" in low:
            return f"{c} — whitelist this server's IP on the Delta API key"
        if "invalidapikey" in low or "invalid_api_key" in low:
            return f"{c} — the API key is wrong (or was deleted on Delta)"
        if "signature" in low and "expired" in low:
            return f"{c} — this server's clock is off; request timestamp rejected"
        if "signature" in low:
            return f"{c} — the API secret doesn't match the key; paste the secret again"
        if "unauthorized" in low or "permission" in low:
            return f"{c} — the key lacks a permission (enable Trading on Delta)"
        return c


class LiveClient:
    def __init__(
        self,
        api_key: str,
        api_secret: str,
        settings: Settings | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._key = api_key
        self._secret = api_secret
        self._http = httpx.AsyncClient(
            base_url=self.settings.delta_api_base,
            timeout=10.0,
            headers={"User-Agent": USER_AGENT},
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _call(
        self, method: str, path: str, params: dict[str, Any] | None = None, body: Any = None
    ) -> Any:
        query = f"?{urlencode(params)}" if params else ""
        payload = json.dumps(body, separators=(",", ":")) if body is not None else ""
        ts = str(int(time.time()))
        headers = {
            "api-key": self._key,
            "timestamp": ts,
            "signature": sign(self._secret, method + ts + path + query + payload),
            "Content-Type": "application/json",
        }
        r = await self._http.request(method, path + query, content=payload, headers=headers)
        try:
            data = r.json()
        except ValueError:
            data = r.text
        if r.status_code >= 400 or (isinstance(data, dict) and data.get("success") is False):
            raise DeltaError(r.status_code, data)
        return data.get("result") if isinstance(data, dict) else data

    # ---- reads ----------------------------------------------------------- #
    async def positions(self) -> list[dict[str, Any]]:
        return list(await self._call("GET", "/v2/positions/margined") or [])

    async def wallet(self) -> list[dict[str, Any]]:
        return list(await self._call("GET", "/v2/wallet/balances") or [])

    async def open_stop_orders(self) -> list[dict[str, Any]]:
        return list(
            await self._call(
                "GET", "/v2/orders", {"states": "open,pending", "order_types": "all_stop"}
            )
            or []
        )

    async def fills(self, product_ids: list[int], page_size: int = 20) -> list[dict[str, Any]]:
        ids = ",".join(str(i) for i in product_ids[:10])
        return list(
            await self._call("GET", "/v2/fills", {"product_ids": ids, "page_size": page_size}) or []
        )

    # ---- orders (reduce-only, kill-switched) ------------------------------ #
    async def _place(self, order: dict[str, Any]) -> dict[str, Any]:
        if order.get("reduce_only") is not True:
            raise OrderRefused("refusing order without reduce_only=true")
        if not self.settings.live_trading_enabled:
            raise OrderRefused("live trading disabled (LIVE_TRADING_ENABLED=false)")
        order.setdefault("client_order_id", uuid.uuid4().hex[:32])
        return dict(await self._call("POST", "/v2/orders", body=order))

    async def close_ioc(
        self, product_id: int, side: Side, size: int, limit_price: float
    ) -> dict[str, Any]:
        return await self._place(
            {
                "product_id": product_id,
                "size": int(size),
                "side": side,
                "order_type": "limit_order",
                "limit_price": _px(limit_price),
                "time_in_force": "ioc",
                "reduce_only": True,
            }
        )

    async def close_market(self, product_id: int, side: Side, size: int) -> dict[str, Any]:
        return await self._place(
            {
                "product_id": product_id,
                "size": int(size),
                "side": side,
                "order_type": "market_order",
                "reduce_only": True,
            }
        )

    async def place_stop(
        self, product_id: int, side: Side, size: int, stop_price: float
    ) -> dict[str, Any]:
        """Exchange-side backstop: a reduce-only stop-market on mark price. Fires even
        when this server is down."""
        return await self._place(
            {
                "product_id": product_id,
                "size": int(size),
                "side": side,
                "order_type": "market_order",
                "stop_order_type": "stop_loss_order",
                "stop_price": _px(stop_price),
                "stop_trigger_method": "mark_price",
                "reduce_only": True,
            }
        )

    async def place_bracket(
        self,
        product_id: int,
        symbol: str,
        close_side: Side,
        sl: float | None,
        tp: float | None,
        tick: float,
    ) -> str:
        """Position bracket on Delta (SL + target on the mark, one-cancels-other, auto-resizes
        with the position). Returns "market" or "limit" — which kind Delta accepted.

        Close-only by construction: a position bracket can only close the position it is
        attached to (Delta has no reduce_only flag on this endpoint). Still kill-switched.
        Market first (can't be skipped); if Delta refuses market brackets, fall back to a
        limit so far through the book it fills like one: sell at 1 tick, buy at 3x trigger.
        ponytail: 3x may exceed Delta's price band on some strikes — the rejection then alerts.
        """
        if not self.settings.live_trading_enabled:
            raise OrderRefused("live trading disabled (LIVE_TRADING_ENABLED=false)")

        def side_order(trigger: float, limit: bool) -> dict[str, Any]:
            o: dict[str, Any] = {"order_type": "market_order", "stop_price": _px(trigger)}
            if limit:
                far = tick if close_side == "sell" else trigger * 3
                o.update(order_type="limit_order", limit_price=_px(far))
            return o

        def body(limit: bool) -> dict[str, Any]:
            b: dict[str, Any] = {
                "product_id": product_id,
                "product_symbol": symbol,
                "bracket_stop_trigger_method": "mark_price",
            }
            if sl is not None:
                b["stop_loss_order"] = side_order(sl, limit)
            if tp is not None:
                b["take_profit_order"] = side_order(tp, limit)
            return b

        try:
            await self._call("POST", "/v2/orders/bracket", body=body(limit=False))
            return "market"
        except DeltaError:
            await self._call("POST", "/v2/orders/bracket", body=body(limit=True))
            return "limit"

    async def cancel(self, product_id: int, order_id: int) -> None:
        # Cancelling only ever REMOVES risk-reducing orders we placed; no kill-switch gate so
        # a disabled switch can still clean up.
        await self._call("DELETE", "/v2/orders", body={"id": order_id, "product_id": product_id})


def _px(price: float) -> str:
    return f"{price:.10f}".rstrip("0").rstrip(".")
