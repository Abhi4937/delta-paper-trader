"""Drive the live path end to end against the fake exchange. Prints PASS/FAIL per step.

Order: 1) uv run python scripts/live_e2e/fake_delta.py      (fake Delta on :8099)
       2) uv run python scripts/live_e2e/run_live_e2e.py    (backend on :8010)
       3) uv run python scripts/live_e2e/drive_e2e.py       (this; exits 1 on any FAIL)
Stop the runner with Ctrl+C afterwards — it removes the fake key and the test data.
Checks: tracking, arm refused without an SL, reduce-only native stops, server SL exits ALL
legs (shorts bought back), leftover stops cancelled, a Delta-side stop cascades the exit.
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


def clean_slate():
    """Leftover open groups keep their SL/arm state (correct app behaviour); neutralise them."""
    st = c.get(f"{API}/api/state").json()
    for p in st["positions"]:
        if p.get("source") == "live" and p["status"] == "open" and not p.get("exiting"):
            c.post(f"{API}/api/live/groups/{p['id']}/arm", json={"armed": False})
            c.patch(
                f"{API}/api/strategies/{p['id']}/risk",
                json={"stop_loss_amount": None, "stop_loss_pct_of_margin": None},
            )
            for lg in p["legs"]:
                c.patch(f"{API}/api/legs/{lg['id']}/risk", json={"stop_pnl": None})


def scenario_server_sl():
    if "--no-reset" not in sys.argv:
        c.post(f"{FAKE}/fake/reset")
    wait(live_group)
    clean_slate()
    g = wait(live_group)
    check("live group mirrored from Delta", g is not None)
    status = wait(lambda: (s := c.get(f"{API}/api/live/status").json())["checks"] and s, 30)
    check(
        "liquidation check computed by guard",
        bool(status),
        str(status and list(status["checks"].values())[0]["verdict"]),
    )
    check(
        "no false 'untracked' alerts",
        not any(a["key"].startswith("untracked") for a in (status or {}).get("alerts", [])),
    )

    r = c.post(f"{API}/api/live/groups/{g['id']}/arm", json={"armed": True})
    check("arming without any SL is refused", r.status_code == 400, r.text)

    r2 = c.patch(f"{API}/api/strategies/{g['id']}/risk", json={"auto_exit": True})
    check("paper risk PATCH cannot arm a live group", r2.status_code == 409, r2.text)

    rp = c.patch(f"{API}/api/strategies/{g['id']}/risk", json={"stop_loss_amount": 1000})
    check("basket SL saved", rp.status_code == 200, f"{rp.status_code} {rp.text[:120]}")
    r = c.post(f"{API}/api/live/groups/{g['id']}/arm", json={"armed": True})
    check("arm with basket SL", r.status_code == 200, r.text[:200])
    fk = c.get(f"{FAKE}/fake/state").json()
    stops = [o for o in fk["orders"].values() if o["state"] == "open"]
    check("one resting stop per leg on Delta", len(stops) == 2, str(len(stops)))
    check(
        "stops are reduce-only stop-market on mark price",
        all(
            o["reduce_only"] is True
            and o["order_type"] == "market_order"
            and o["stop_trigger_method"] == "mark_price"
            and o["side"] == "buy"
            for o in stops
        ),
    )
    g = live_group()
    check("stop prices shown on legs", all(lg["stopPrice"] for lg in g["legs"]))

    # tighten one leg's SL below its current loss -> server SL must exit ALL legs
    worst = min(g["legs"], key=lambda lg: lg["pnl"])
    print("   leg P&Ls:", [round(lg["pnl"], 4) for lg in g["legs"]])
    if worst["pnl"] < 0:
        c.patch(f"{API}/api/legs/{worst['id']}/risk", json={"stop_pnl": -1e-6})
    else:
        # both legs in profit: use a basket stop below current P&L instead
        c.patch(f"{API}/api/strategies/{g['id']}/risk", json={"stop_loss_amount": 0.000001})
    flat = wait(
        lambda: all(
            p["size"] == 0 for p in c.get(f"{FAKE}/fake/state").json()["positions"].values()
        )
    )
    check("SL hit -> every leg closed on Delta", bool(flat))
    fk = c.get(f"{FAKE}/fake/state").json()
    closes = [o for o in fk["log"] if "stop_order_type" not in o]
    check(
        "closes were reduce-only IOC, shorts bought back",
        closes and all(o["reduce_only"] is True and o["side"] == "buy" for o in closes),
        str([(o["order_type"], o.get("limit_price")) for o in closes]),
    )
    closed = wait(lambda: live_group() is None, 15)
    check("group closed in the app", bool(closed))
    fk = c.get(f"{FAKE}/fake/state").json()
    check(
        "leftover stops cancelled after flat",
        not [o for o in fk["orders"].values() if o["state"] == "open"],
    )


def scenario_native_stop():
    c.post(f"{FAKE}/fake/reset")
    g = wait(live_group)
    check("[native] new group after reset", g is not None)
    c.patch(f"{API}/api/strategies/{g['id']}/risk", json={"stop_loss_amount": 1000})
    r = c.post(f"{API}/api/live/groups/{g['id']}/arm", json={"armed": True})
    check("[native] armed", r.status_code == 200, r.text[:200])
    pid = g["legs"][0]["productId"]
    c.post(f"{FAKE}/fake/fire_stop/{pid}")
    flat = wait(
        lambda: all(
            p["size"] == 0 for p in c.get(f"{FAKE}/fake/state").json()["positions"].values()
        )
    )
    check("[native] Delta stop on one leg -> app closed the other leg", bool(flat))
    st = c.get(f"{API}/api/state").json()
    last = [p for p in st["positions"] if p.get("source") == "live"][0]
    reasons = sorted(lg["exitReason"] or "" for lg in last["legs"])
    check("[native] exit reasons recorded", "native stop" in reasons, str(reasons))


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
    scenario_native_stop()
    scenario_journal()
    scenario_account_and_precheck()
    print("FAILURES:", fails)
    sys.exit(1 if fails else 0)
