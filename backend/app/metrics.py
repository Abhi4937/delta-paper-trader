"""Prometheus metrics — the backend APM layer scraped by Netdata.

Exposes an HTTP request-latency histogram (labelled by the matched route template,
NOT the raw path, to bound cardinality) plus app-level gauges (Delta feed freshness
and tracked open positions). Served at GET /metrics, which is intentionally NOT in
the Caddy route map, so it is only reachable on the internal docker network
(Netdata scrapes backend:8010/metrics). Observe-only: no secrets, no trade path.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Gauge,
    Histogram,
    generate_latest,
)
from starlette.middleware.base import BaseHTTPMiddleware

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from fastapi import FastAPI
    from starlette.requests import Request
    from starlette.responses import Response

REGISTRY = CollectorRegistry(auto_describe=True)

REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency by method, matched route template and status code.",
    ["method", "route", "status"],
    registry=REGISTRY,
)
FEED_FRESH = Gauge("app_feed_fresh", "1 if the Delta market-data feed is fresh, else 0.", registry=REGISTRY)
FEED_AGE = Gauge("app_feed_age_seconds", "Seconds since the last Delta feed message (-1 if unknown).", registry=REGISTRY)
OPEN_POSITIONS = Gauge("app_open_positions", "Currently tracked open positions across all users.", registry=REGISTRY)


class PrometheusMiddleware(BaseHTTPMiddleware):
    """Time every request into REQUEST_LATENCY, labelled by the matched route template."""

    async def dispatch(
        self, request: "Request", call_next: "Callable[[Request], Awaitable[Response]]"
    ) -> "Response":
        start = time.perf_counter()
        response = await call_next(request)
        route = request.scope.get("route")
        template = getattr(route, "path", None) or "<unmatched>"
        REQUEST_LATENCY.labels(request.method, template, str(response.status_code)).observe(
            time.perf_counter() - start
        )
        return response


def render_metrics(app: "FastAPI") -> tuple[bytes, str]:
    """Refresh app-level gauges from app.state, then render the exposition payload."""
    market = getattr(app.state, "market", None)
    if market is not None:
        status = market.feed_status()
        FEED_FRESH.set(1.0 if status.get("fresh") else 0.0)
        age = status.get("age_seconds")
        FEED_AGE.set(float(age) if age is not None else -1.0)
    series = getattr(app.state, "sim_series", None)
    if series is not None:
        OPEN_POSITIONS.set(float(len(series)))
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST
