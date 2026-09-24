"""Drive the live path end to end against the fake exchange. Prints PASS/FAIL per step.

Order: 1) uv run python scripts/live_e2e/fake_delta.py      (fake Delta on :8099)
       2) uv run python scripts/live_e2e/run_live_e2e.py    (backend on :8010)
       3) uv run python scripts/live_e2e/drive_e2e.py       (this; exits 1 on any FAIL)
Stop the runner with Ctrl+C afterwards — it removes the fake key and the test data.
Checks: tracking; arm refused without an SL; crossed / too-close SLs refused; leg SL+target
as premium triggers become a Delta bracket on the mark; the mark crossing a leg SL exits ALL
legs (reduce-only buy-backs); Delta's bracket SL and target each cascade to the other legs;
basket target; journal; key test; wallet; pre-trade check.
"""

import sys
import time

import httpx

API = "http://127.0.0.1:8010"
FAKE = "http://127.0.0.1:8099"
c = httpx.Client(timeout=30)
fails = 0


def check(name, cond, detail=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail else ""), flush=True)
    fails += 0 if cond else 1


def live_group():
    st = c.get(f"{API}/api/state").json()
    opens = [p for p in st["positions"] if p.get("source") == "live" and p["status"] == "open"]
    return opens[0] if opens else None


def wait(pred, secs=40):
    end = time.time() + secs
    while time.time() < end:
        v = pred()
        if v:
            return v
        time.sleep(1)
    return None


def fake():
    return c.get(f"{FAKE}/fake/state").json()


def flat():
    return all(p["size"] == 0 for p in fake()["positions"].values())


def set_mark(symbol, price=None):
    q = {"symbol": symbol} if price is None else {"symbol": symbol, "price": price}
    return c.post(f"{API}/e2e/mark", params=q)


def leg_bracket(leg_id, **kw):
    return c.patch(f"{API}/api/live/legs/{leg_id}/bracket", json=kw)


def basket(gid, **kw):
    return c.patch(f"{API}/api/live/groups/{gid}/basket", json=kw)


def fresh_group():
    """Reset the fake to two short legs and hand back a clean, disarmed live group."""
    c.post(f"{FAKE}/fake/reset")
    time.sleep(3)  # let the sync close the previous group and mirror the new positions
    g = wait(live_group)
    for lg in g["legs"]:
        set_mark(lg["symbol"])
    if g["autoExit"]:
        c.post(f"{API}/api/live/groups/{g['id']}/arm", json={"armed": False})
    basket(g["id"], sl_amount=None, sl_pct=None, tp_amount=None, tp_pct=None)
    for lg in g["legs"]:
        leg_bracket(lg["id"], sl_price=None, tp_price=None)
    return wait(live_group)


def scenario_server_sl():
    g = fresh_group()
    check("live group mirrored from Delta", g is not None)
    check(
        "close-now P&L sent for the open group (book-based, below mark P&L)",
        isinstance(g.get("exitPnl"), (int, float)) and g["exitPnl"] <= g["pnl"] + 1e-9,
        f"mark {g.get('pnl')} close-now {g.get('exitPnl')}",
    )
    status = wait(lambda: (s := c.get(f"{API}/api/live/status").json())["checks"] and s, 30)
    check("liquidation check computed by guard", bool(status))
    check(
        "no false 'untracked' alerts",
        not any(a["key"].startswith("untracked") for a in (status or {}).get("alerts", [])),
    )
    r = c.post(f"{API}/api/live/groups/{g['id']}/arm", json={"armed": True})
    check("arming without any SL is refused", r.status_code == 400, r.text)
    r = c.patch(f"{API}/api/strategies/{g['id']}/risk", json={"stop_loss_amount": 5})
    check("paper risk route cannot edit a live group", r.status_code == 409, r.text)

    leg = g["legs"][0]  # a SOLD leg: its SL must sit ABOVE the mark
    m = leg["mark"]
    r = leg_bracket(leg["id"], sl_price=round(m * 0.9, 1))
    check(
        "crossed SL refused (sold leg, SL below mark)",
        r.status_code == 409 and "above" in r.text,
        r.text[:160],
    )
    r = leg_bracket(leg["id"], sl_price=round(m * 1.001, 1))
    check("SL inside the 1% buffer refused", r.status_code == 409, r.text[:160])
    sl, tp = round(m * 1.5, 1), round(m * 0.5, 1)
    r = leg_bracket(leg["id"], sl_price=sl, tp_price=tp)
    check("valid SL + target (premium triggers) saved", r.status_code == 200, r.text[:160])
    r = basket(g["id"], sl_amount=1000)
    check("basket SL saved", r.status_code == 200, r.text[:160])
    r = c.post(f"{API}/api/live/groups/{g['id']}/arm", json={"armed": True})
    check("armed", r.status_code == 200, r.text[:160])

    brk = [x["bracket"] for x in fake()["log"] if "bracket" in x]
    mine = [b for b in brk if b["product_id"] == leg["productId"]]
    check(
        "Delta bracket placed on mark with SL + target at the leg's prices",
        bool(mine)
        and mine[-1]["bracket_stop_trigger_method"] == "mark_price"
        and float(mine[-1]["stop_loss_order"]["stop_price"]) == sl
        and float(mine[-1]["take_profit_order"]["stop_price"]) == tp
        and mine[-1]["stop_loss_order"]["order_type"] == "market_order",
        str(mine[-1:]),
    )
    other = g["legs"][1]
    ob = [b for b in brk if b["product_id"] == other["productId"]]
    check(
        "leg without its own SL gets the basket-derived SL, no target",
        bool(ob) and "stop_loss_order" in ob[-1] and "take_profit_order" not in ob[-1],
        str(ob[-1:]),
    )
    g = live_group()
    check(
        "bracket prices shown on legs",
        g["legs"][0]["stopPrice"] == sl
        and g["legs"][0]["slPrice"] == sl
        and g["legs"][0]["tpPrice"] == tp,
    )

    set_mark(leg["symbol"], sl + 1)  # the market crosses the SL
    check("mark crosses leg SL -> EVERY leg closed on Delta", bool(wait(flat)))
    closes = [o for o in fake()["log"] if "bracket" not in o and "stop_order_type" not in o]
    check(
        "closes were reduce-only buy-backs",
        bool(closes) and all(o["reduce_only"] is True and o["side"] == "buy" for o in closes),
    )
    check("group closed in the app", bool(wait(lambda: live_group() is None, 15)))
    check(
        "leftover brackets cancelled",
        not [o for o in fake()["orders"].values() if o["state"] == "open"],
    )
    set_mark(leg["symbol"])


def scenario_delta_bracket(kind):
    g = fresh_group()
    leg = g["legs"][0]
    m = leg["mark"]
    leg_bracket(leg["id"], sl_price=round(m * 1.5, 1), tp_price=round(m * 0.5, 1))
    basket(g["id"], sl_amount=1000)
    c.post(f"{API}/api/live/groups/{g['id']}/arm", json={"armed": True})
    c.post(f"{FAKE}/fake/fire_stop/{leg['productId']}", params={"kind": kind})
    label = "SL" if kind == "stop_loss_order" else "target"
    check(f"[Delta bracket {label}] fired on one leg -> app closed the other", bool(wait(flat)))
    wait(lambda: live_group() is None, 15)
    last = [p for p in c.get(f"{API}/api/state").json()["positions"] if p.get("source") == "live"][
        0
    ]
    reasons = [lg["exitReason"] or "" for lg in last["legs"]]
    check(
        f"[Delta bracket {label}] exit reason recorded",
        f"Delta bracket {label}" in reasons,
        str(reasons),
    )


def scenario_basket_target():
    g = fresh_group()
    basket(g["id"], sl_amount=1000)
    r = basket(g["id"], tp_amount=0.001 if g["pnl"] < 0 else g["pnl"] + 0.001)
    check("[basket target] valid target saved", r.status_code == 200, r.text[:160])
    c.post(f"{API}/api/live/groups/{g['id']}/arm", json={"armed": True})
    for lg in g["legs"]:  # both short: premiums collapse -> profit
        set_mark(lg["symbol"], round(lg["entry"] * 0.1, 1))
    check("[basket target] profit reaches target -> every leg closed", bool(wait(flat)))
    for lg in g["legs"]:
        set_mark(lg["symbol"])


def scenario_journal():
    j = c.get(f"{API}/api/live/journal").json()
    closed = [t for t in j["trades"] if t["status"] == "closed"]
    check("[journal] closed trades have a frozen snapshot", bool(closed), str(len(closed)))
    t = closed[0]
    keys = [
        "openedAt",
        "closedAt",
        "durationSeconds",
        "pnl",
        "fees",
        "maxMtm",
        "minMtm",
        "maxDrawdown",
        "closeReason",
        "legs",
    ]
    check(
        "[journal] snapshot has every field",
        all(k in t for k in keys),
        str([k for k in keys if k not in t]),
    )
    check(
        "[journal] legs carry entry + exit",
        all(lg["exit"] is not None and lg["entry"] for lg in t["legs"]),
    )
    check(
        "[journal] realised P&L = sum of leg P&L",
        abs(t["pnl"] - sum(lg["pnl"] for lg in t["legs"])) < 1e-9,
        f"{t['pnl']}",
    )
    check("[journal] live log present", any(lg["action"] == "LIVE_CLOSE" for lg in j["logs"]))
    lg0 = t["legs"][0]
    detail = [
        "entryAt",
        "entryFees",
        "entryMargin",
        "entrySlippage",
        "exitSlippage",
        "exitMargin",
        "markAtExit",
    ]
    check(
        "[journal] entry/exit detail recorded",
        all(lg0.get(k) is not None for k in detail),
        str({k: lg0.get(k) for k in detail}),
    )
    check(
        "[journal] net P&L subtracts entry + exit brokerage",
        abs(lg0["pnl"] - (lg0["grossPnl"] - lg0["entryFees"] - lg0["fees"])) < 1e-9,
    )
    cd = c.get(f"{API}/api/live/journal/{t['id']}/candles").json()
    rows = cd["legs"][0]["candles"] if cd.get("legs") else []
    check(
        "[journal] 1m candles per leg: mark OHLC + P&L OHLC + bid/ask OHLC, frozen at close",
        bool(rows) and len(rows[0]) == 17 and any(r[9] is not None for r in rows),
        f"{len(rows)} candles, first={rows[0] if rows else None}",
    )
    check(
        "[journal] best bid/ask at entry recorded",
        lg0.get("entryBid") is not None and lg0.get("entryAsk") is not None,
        str((lg0.get("entryBid"), lg0.get("entryAsk"))),
    )
    paper_logs = c.get(f"{API}/api/state").json()["logs"]
    check(
        "[journal] paper logs contain no live entries",
        not any(lg["action"].startswith("LIVE") for lg in paper_logs),
    )


def scenario_account_and_precheck():
    r = c.post(f"{API}/api/live/test-key")
    check("[account] Test Delta key passes", r.status_code == 200 and r.json()["ok"], r.text[:160])
    a = c.get(f"{API}/api/live/account").json()
    check(
        "[account] wallet read: balance + available",
        a == {"balance": 5000.0, "available": 4200.0},
        str(a),
    )
    c.post(f"{FAKE}/fake/reset")
    g = wait(live_group)
    legs = [{"symbol": lg["symbol"], "side": "sell", "qty": 10} for lg in g["legs"]]
    r = c.post(f"{API}/api/live/precheck", json={"legs": legs, "basket_sl": 20})
    ok = r.status_code == 200 and r.json()["check"]["verdict"] in ("green", "yellow", "red")
    check("[precheck] small basket with SL gets a verdict", ok, r.text[:200])
    check(
        "[precheck] small basket is green",
        ok and r.json()["check"]["verdict"] == "green",
        r.json()["check"]["reason"] if ok else "",
    )
    big = [{**lg, "qty": 50000} for lg in legs]
    r = c.post(f"{API}/api/live/precheck", json={"legs": big, "basket_sl": None})
    check(
        "[precheck] huge basket with no SL is red",
        r.status_code == 200 and r.json()["check"]["verdict"] == "red",
        r.text[:200],
    )


if __name__ == "__main__":
    scenario_server_sl()
    scenario_delta_bracket("stop_loss_order")
    scenario_delta_bracket("take_profit_order")
    scenario_basket_target()
    scenario_journal()
    scenario_account_and_precheck()
    print("FAILURES:", fails)
    sys.exit(1 if fails else 0)
