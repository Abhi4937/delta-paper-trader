"""Unit tests for the per-IP rate limiter."""
# Mocks stand in for Starlette Request/call_next; the duck-typed shapes are intentional.
# mypy: disable-error-code="arg-type"

from __future__ import annotations

from starlette.responses import PlainTextResponse, Response

from app.ratelimit import RateLimiter


class _Client:
    def __init__(self, host: str) -> None:
        self.host = host


class _Req:
    def __init__(self, host: str = "1.1.1.1") -> None:
        self.client = _Client(host)


async def _ok(_req: object) -> Response:
    return PlainTextResponse("ok")


async def test_blocks_after_limit() -> None:
    rl = RateLimiter(3)
    req = _Req()
    codes = [(await rl(req, _ok)).status_code for _ in range(5)]
    assert codes == [200, 200, 200, 429, 429]


async def test_per_ip_isolated() -> None:
    rl = RateLimiter(1)
    a, b = _Req("1.1.1.1"), _Req("2.2.2.2")
    assert (await rl(a, _ok)).status_code == 200
    assert (await rl(a, _ok)).status_code == 429  # a exhausted its window
    assert (await rl(b, _ok)).status_code == 200  # b has its own bucket
