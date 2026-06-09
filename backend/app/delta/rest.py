"""Minimal async Delta Exchange India REST client.

READ-ONLY usage only — this client is never used to place/modify/cancel orders.
Implements Delta's HMAC-SHA256 signing:
    signature = hex(hmac_sha256(api_secret, method + timestamp + path + query + body))
with headers: api-key, timestamp, signature, User-Agent.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any
from urllib.parse import urlencode

import httpx

from app.config import Settings, get_settings

USER_AGENT = "paper-trader/0.1"


def sign(secret: str, message: str) -> str:
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


class DeltaRestClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._client = httpx.AsyncClient(
            base_url=self.settings.delta_api_base,
            timeout=15.0,
            headers={"User-Agent": USER_AGENT},
        )

    def _auth_headers(
        self, method: str, path: str, query: str = "", body: str = ""
    ) -> dict[str, str]:
        timestamp = str(int(time.time()))
        signature = sign(
            self.settings.delta_api_secret, method + timestamp + path + query + body
        )
        return {
            "api-key": self.settings.delta_api_key,
            "timestamp": timestamp,
            "signature": signature,
            "Content-Type": "application/json",
        }

    async def get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        signed: bool = False,
    ) -> Any:
        query = f"?{urlencode(params)}" if params else ""
        headers = self._auth_headers("GET", path, query) if signed else None
        resp = await self._client.get(path + query, headers=headers)
        resp.raise_for_status()
        return resp.json()

    async def post(
        self,
        path: str,
        *,
        json_body: Any = None,
        signed: bool = False,
        base_url: str | None = None,
    ) -> Any:
        """Signed/unsigned POST. Body is serialized once so the HMAC matches bytes sent.

        Used READ-ONLY for `/v2/orders/estimate_margin/basket` (a non-mutating
        estimator). This client never places real orders.
        """
        body = json.dumps(json_body, separators=(",", ":")) if json_body is not None else ""
        headers = {"Content-Type": "application/json"}
        if signed:
            headers.update(self._auth_headers("POST", path, "", body))
        url = f"{base_url}{path}" if base_url else path
        resp = await self._client.post(url, content=body, headers=headers)
        resp.raise_for_status()
        return resp.json()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> DeltaRestClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()
