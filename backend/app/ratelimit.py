"""Per-IP fixed-window rate limiting (in-process, no extra deps).

Mitigates request floods / brute-force against the API. Single-process scope; for
multi-instance production put a CDN/proxy limit (or a Redis counter) in front too.
Login/signup themselves go through Supabase, which rate-limits its own auth endpoints.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

_WINDOW = 60.0
_MAX_TRACKED = 50_000  # bound memory; prune when exceeded


class RateLimiter:
    def __init__(self, limit_per_min: int) -> None:
        self.limit = limit_per_min
        self._hits: dict[str, tuple[float, int]] = {}

    async def __call__(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        ip = request.client.host if request.client else "?"
        now = time.monotonic()
        start, count = self._hits.get(ip, (now, 0))
        if now - start >= _WINDOW:
            start, count = now, 0
        count += 1
        self._hits[ip] = (start, count)

        if len(self._hits) > _MAX_TRACKED:  # drop stale windows
            self._hits = {k: v for k, v in self._hits.items() if now - v[0] < _WINDOW}

        if count > self.limit:
            retry = max(int(_WINDOW - (now - start)), 1)
            return JSONResponse(
                {"detail": "rate limit exceeded"},
                status_code=429,
                headers={"Retry-After": str(retry)},
            )
        return await call_next(request)
