"""Unit tests for the Prometheus /metrics APM layer."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app import metrics as m
from app.main import app

client = TestClient(app)


class _FakeMarket:
    """Stand-in for MarketDataIngestor: only feed_status() is used by /metrics."""

    def __init__(self, *, fresh: bool, age: float | None) -> None:
        self._fresh, self._age = fresh, age

    def feed_status(self) -> dict[str, object]:
        return {"connected": True, "age_seconds": self._age, "fresh": self._fresh}


def test_metrics_endpoint_exposes_prometheus_text() -> None:
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    assert "http_request_duration_seconds" in resp.text
    assert "app_feed_fresh" in resp.text


def test_feed_fresh_gauge_reflects_market_state() -> None:
    app.state.market = _FakeMarket(fresh=True, age=1.2)
    client.get("/metrics")
    assert m.REGISTRY.get_sample_value("app_feed_fresh") == 1.0

    app.state.market = _FakeMarket(fresh=False, age=30.0)
    client.get("/metrics")
    assert m.REGISTRY.get_sample_value("app_feed_fresh") == 0.0


def test_request_histogram_increments_per_request() -> None:
    labels = {"method": "GET", "route": "/health", "status": "200"}
    before = m.REGISTRY.get_sample_value("http_request_duration_seconds_count", labels) or 0.0
    client.get("/health")
    after = m.REGISTRY.get_sample_value("http_request_duration_seconds_count", labels) or 0.0
    assert after == before + 1.0
