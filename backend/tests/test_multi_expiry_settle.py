"""Orchestration of settle_expired_legs: single- vs multi-expiry close behaviour.

The settle/close money path itself is integration-validated on the dev DB; here we mock
close_leg/close_position/fetch_settlement_prices/leg_is_expired to assert the BRANCHING:
- single-expiry → every leg settles (fee-free); no market close.
- multi-expiry → nearest expiry settles, then the WHOLE strategy closes at market ("expiry-close").
- a missing settlement price defers the whole position (survivors are NOT closed yet).
"""
from types import SimpleNamespace

import app.sim.service as svc


class _Result:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return self

    def all(self):
        return self._items


class _Session:
    def __init__(self, positions):
        self._positions = positions

    async def execute(self, *a, **k):
        return _Result(self._positions)


def _leg(lid, expiry):
    return SimpleNamespace(id=lid, symbol=f"SYM-{lid}", expiry=expiry, status="open")


def _pos(pid, legs):
    return SimpleNamespace(id=pid, user_id="u", legs=legs, status="open")


def _wire(monkeypatch, pos, prices, expired_when):
    """monkeypatch the helpers; return (settled_calls, closed_calls) recorders."""
    monkeypatch.setattr(svc, "leg_is_expired", lambda lg, now: expired_when(lg))

    async def fake_prices(_client):
        return prices

    monkeypatch.setattr(svc, "fetch_settlement_prices", fake_prices)

    settled, closed = [], []

    async def fake_close_leg(session, uid, app, pid, lid, reason, settle_price=None):
        settled.append((lid, reason, settle_price))
        for lg in pos.legs:
            if lg.id == lid:
                lg.status = "closed"
        return True

    async def fake_close_position(session, uid, app, pid, reason):
        closed.append((pid, reason))
        for lg in pos.legs:
            if lg.status == "open":
                lg.status = "closed"
        pos.status = "closed"
        return True

    monkeypatch.setattr(svc, "close_leg", fake_close_leg)
    monkeypatch.setattr(svc, "close_position", fake_close_position)
    return settled, closed


async def test_multi_expiry_closes_survivors_at_market(monkeypatch):
    pos = _pos("p1", [_leg("a", "near"), _leg("b", "far")])
    settled, closed = _wire(monkeypatch, pos, {"SYM-a": 100.0}, lambda lg: lg.expiry == "near")
    n = await svc.settle_expired_legs(_Session([pos]), SimpleNamespace(delta=None))
    assert n == 1
    assert settled == [("a", "settlement", 100.0)]      # nearest leg settled fee-free
    assert closed == [("p1", "expiry-close")]           # survivor closed at market


async def test_single_expiry_settles_all_no_market_close(monkeypatch):
    pos = _pos("p2", [_leg("a", "near"), _leg("b", "near")])
    settled, closed = _wire(monkeypatch, pos, {"SYM-a": 1.0, "SYM-b": 2.0}, lambda lg: True)
    n = await svc.settle_expired_legs(_Session([pos]), SimpleNamespace(delta=None))
    assert n == 2
    assert {c[0] for c in settled} == {"a", "b"}
    assert closed == []  # all legs settled → no separate market close


async def test_missing_price_defers_whole_position(monkeypatch):
    pos = _pos("p3", [_leg("a", "near"), _leg("b", "far")])
    settled, closed = _wire(monkeypatch, pos, {}, lambda lg: lg.expiry == "near")  # SYM-a unpublished
    n = await svc.settle_expired_legs(_Session([pos]), SimpleNamespace(delta=None))
    assert n == 0
    assert settled == []
    assert closed == []  # never close survivors before the expired leg settles
