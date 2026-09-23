"""Fake Delta private API for an end-to-end live-path check (no real money).

Two SHORT BTC option legs on the nearest real expiry (so the real market feed has marks).
Accepts orders, keeps resting stop orders, fills closes, records fills.
POST /fake/fire_stop/{product_id} simulates Delta triggering our resting stop on that leg.
Asserts every order is reduce_only. Run from backend/: uv run python scripts/live_e2e/fake_delta.py
"""

import itertools

import httpx
import uvicorn
from fastapi import FastAPI, Request

app = FastAPI()
ids = itertools.count(1000)
S: dict = {"positions": {}, "orders": {}, "fills": [], "log": []}


def seed() -> None:
    r = httpx.get(
        "https://api.india.delta.exchange/v2/tickers",
        params={"contract_types": "call_options,put_options", "underlying_asset_symbols": "BTC"},
        timeout=20,
    ).json()["result"]
    exp = sorted({t["symbol"].split("-")[-1] for t in r}, key=lambda e: (e[4:], e[2:4], e[:2]))
    # skip today's expiry: pick the 2nd nearest so it doesn't expire mid-test
    e = exp[1]
    rows = [t for t in r if t["symbol"].endswith(e) and float(t.get("mark_price") or 0) > 5]
    spot = float(rows[0]["spot_price"])
    call = min(
        (
            t
            for t in rows
            if t["contract_type"] == "call_options" and float(t["strike_price"]) > spot
        ),
        key=lambda t: float(t["strike_price"]),
    )
    put = max(
        (
            t
            for t in rows
            if t["contract_type"] == "put_options" and float(t["strike_price"]) < spot
        ),
        key=lambda t: float(t["strike_price"]),
    )
    for t in (call, put):
        mark = float(t["mark_price"])
        S["positions"][t["product_id"]] = {
            "product_id": t["product_id"],
            "product_symbol": t["symbol"],
            "size": -10,
            "entry_price": f"{mark:.1f}",
            "mark_price": f"{mark:.1f}",
        }
    print(
        "SEEDED",
        [(p["product_symbol"], p["entry_price"]) for p in S["positions"].values()],
        flush=True,
    )


def ok(result):
    return {"success": True, "result": result}


@app.get("/v2/positions/margined")
def positions():
    return ok([p for p in S["positions"].values() if p["size"] != 0])


@app.get("/v2/wallet/balances")
def wallet():
    return ok([{"asset_symbol": "USD", "balance": "5000"}])


@app.get("/v2/orders")
def orders():
    return ok([o for o in S["orders"].values() if o["state"] == "open"])


@app.get("/v2/fills")
def fills():
    return ok(S["fills"])


def _fill(pid: int, side: str, size: int, price: float, order_id: int) -> None:
    p = S["positions"][pid]
    size = min(size, abs(p["size"]))  # reduce-only: never flips
    p["size"] += size if side == "buy" else -size
    S["fills"].insert(
        0, {"product_id": pid, "price": str(price), "order_id": order_id, "commission": "0.01"}
    )


@app.post("/v2/orders")
async def place(req: Request):
    o = await req.json()
    S["log"].append(o)
    assert o.get("reduce_only") is True, "non reduce-only order reached the exchange!"
    oid = next(ids)
    o["id"] = oid
    pid = o["product_id"]
    if "stop_order_type" in o:
        o["state"] = "open"
        S["orders"][oid] = o
        return ok(o)
    mark = float(S["positions"][pid]["mark_price"])
    px = float(o.get("limit_price") or mark)
    _fill(pid, o["side"], o["size"], px, oid)
    o["state"] = "closed"
    return ok(o)


@app.delete("/v2/orders")
async def cancel(req: Request):
    b = await req.json()
    o = S["orders"].get(b["id"])
    if o:
        o["state"] = "cancelled"
    return ok({})


@app.post("/fake/fire_stop/{pid}")
def fire(pid: int):
    o = next(o for o in S["orders"].values() if o["product_id"] == pid and o["state"] == "open")
    o["state"] = "closed"
    _fill(pid, o["side"], o["size"], float(o["stop_price"]), o["id"])
    return {"fired": o["id"]}


@app.post("/fake/reset")
def reset():
    S.update({"positions": {}, "orders": {}, "fills": [], "log": []})
    seed()
    return {"ok": True}


@app.get("/fake/state")
def state():
    return S


if __name__ == "__main__":
    seed()
    uvicorn.run(app, host="127.0.0.1", port=8099, log_level="warning")
