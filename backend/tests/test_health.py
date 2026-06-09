"""Smoke test for the walking-skeleton backend: health + WS heartbeat."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health() -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "version" in body


def test_ws_heartbeat() -> None:
    with client.websocket_connect("/ws") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "heartbeat"
        assert msg["seq"] == 0
        assert "ts" in msg
