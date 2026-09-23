import uuid
from datetime import UTC, datetime

from app.live.sync import desired_stop, evaluate_live, reconcile, usd_balance
from app.services.chain import normalize_contract

UID = uuid.uuid4()
NOW = datetime(2026, 9, 20, tzinfo=UTC)


def _contract(sym, pid, kind, strike):
    return normalize_contract(
        {
            "symbol": sym,
            "product_id": pid,
            "contract_type": f"{kind}_options",
            "strike_price": str(strike),
            "mark_price": "100",
            "contract_value": "0.001",
            "tick_size": "0.5",
            "spot_price": "100000",
        }
    )


CONTRACTS = {
    "C-BTC-105000-260926": _contract("C-BTC-105000-260926", 1, "call", 105_000),
    "P-BTC-95000-260926": _contract("P-BTC-95000-260926", 2, "put", 95_000),
}


def _run(groups, positions):
    return reconcile(UID, groups, positions, CONTRACTS.get, lambda u: 100_000.0, NOW)


def test_groups_by_underlying_expiry_and_tracks_untracked():
    r = _run(
        [],
        [
            {
                "product_id": 1,
                "product_symbol": "C-BTC-105000-260926",
                "size": -10,
                "entry_price": "120",
            },
            {
                "product_id": 2,
                "product_symbol": "P-BTC-95000-260926",
                "size": -10,
                "entry_price": "110",
            },
            {"product_id": 9, "product_symbol": "BTCUSD", "size": 3, "entry_price": "1"},
        ],
    )
    assert len(r.new_groups) == 1
    g = r.new_groups[0]
    assert (g.underlying, g.expiry, g.source, g.auto_exit) == ("BTC", "2026-09-26", "live", False)
    assert [(lg.side, lg.qty, lg.entry) for lg in g.legs] == [("sell", 10, 120), ("sell", 10, 110)]
    assert r.untracked == ["BTCUSD"]
    assert r.gone == []


def test_size_change_updates_and_flat_leg_is_gone():
    g = _run(
        [],
        [
            {
                "product_id": 1,
                "product_symbol": "C-BTC-105000-260926",
                "size": -10,
                "entry_price": "120",
            },
            {
                "product_id": 2,
                "product_symbol": "P-BTC-95000-260926",
                "size": -10,
                "entry_price": "110",
            },
        ],
    ).new_groups[0]
    r = _run(
        [g],
        [
            {
                "product_id": 1,
                "product_symbol": "C-BTC-105000-260926",
                "size": -20,
                "entry_price": "125",
            }
        ],
    )
    assert r.new_groups == []
    call = next(lg for lg in g.legs if lg.product_id == 1)
    assert (call.qty, call.entry) == (20, 125)
    assert [lg.product_id for _, lg in r.gone] == [2]


def _group(stop_amount=None, leg_stop=None):
    g = _run(
        [],
        [
            {
                "product_id": 1,
                "product_symbol": "C-BTC-105000-260926",
                "size": -10,
                "entry_price": "100",
            },
            {
                "product_id": 2,
                "product_symbol": "P-BTC-95000-260926",
                "size": 10,
                "entry_price": "100",
            },
        ],
    ).new_groups[0]
    g.stop_loss_amount = stop_amount
    g.stop_loss_pct_of_margin = None
    for lg in g.legs:
        lg.stop_pnl = leg_stop
    return g


def test_any_leg_stop_closes_whole_group():
    g = _group(leg_stop=-0.5)
    marks = {1: 160.0, 2: 100.0}  # short call lost (160-100)*10*0.001 = 0.6 > 0.5
    d = evaluate_live(g, lambda lg: marks[lg.product_id], stale=False, grace=False)
    assert d.kind == "close-strategy"


def test_basket_stop_closes_whole_group():
    g = _group(stop_amount=1.0)
    marks = {1: 180.0, 2: 70.0}  # -0.8 + -0.3 = -1.1
    assert evaluate_live(g, lambda lg: marks[lg.product_id], False, False).kind == "close-strategy"
    marks = {1: 150.0, 2: 90.0}  # -0.6
    assert evaluate_live(g, lambda lg: marks[lg.product_id], False, False).kind == "none"


def test_stale_feed_suspends_until_grace_then_enforces_stops():
    g = _group(stop_amount=1.0)
    marks = {1: 180.0, 2: 70.0}
    assert evaluate_live(g, lambda lg: marks[lg.product_id], True, False).kind == "suspended"
    assert evaluate_live(g, lambda lg: marks[lg.product_id], True, True).kind == "close-strategy"


def test_native_stop_from_basket_when_leg_has_none():
    g = _group(stop_amount=1.0)
    short, long_ = g.legs
    assert desired_stop(g, short, 0.5) == 200  # 100 + 1.0 / (10*0.001)
    assert (
        desired_stop(g, long_, 0.5) is None
    )  # long can't lose more than its 1.0 premium... 100-100=0


def test_usd_balance():
    assert (
        usd_balance(
            [{"asset_symbol": "BTC", "balance": "1"}, {"asset_symbol": "USD", "balance": "250.5"}]
        )
        == 250.5
    )
    assert usd_balance([]) is None


def test_feed_gap_never_closes_a_leg_delta_still_holds():
    g = _run([], [
        {"product_id": 1, "product_symbol": "C-BTC-105000-260926", "size": -10, "entry_price": "120"},
    ]).new_groups[0]
    # the feed momentarily can't describe the symbol: leg must stay open, no alert
    r = reconcile(UID, [g], [
        {"product_id": 1, "product_symbol": "C-BTC-105000-260926", "size": -10, "entry_price": "120"},
    ], lambda sym: None, lambda u: 0.0, NOW)
    assert r.gone == [] and r.untracked == [] and r.new_groups == []


def test_exiting_group_never_absorbs_a_reopened_position():
    g = _run([], [
        {"product_id": 1, "product_symbol": "C-BTC-105000-260926", "size": -10, "entry_price": "120"},
    ]).new_groups[0]
    g.exiting = True
    old = g.legs[0]
    old.status = "closed"  # the exit closed it
    r = _run([g], [
        {"product_id": 1, "product_symbol": "C-BTC-105000-260926", "size": -10, "entry_price": "130"},
    ])
    assert len(r.new_groups) == 1 and r.new_groups[0] is not g
    assert r.new_groups[0].auto_exit is False
    assert len(g.legs) == 1


def test_exiting_group_legs_are_not_resized_mid_exit():
    g = _run([], [
        {"product_id": 1, "product_symbol": "C-BTC-105000-260926", "size": -10, "entry_price": "120"},
    ]).new_groups[0]
    g.exiting = True
    r = _run([g], [
        {"product_id": 1, "product_symbol": "C-BTC-105000-260926", "size": -4, "entry_price": "120"},
    ])
    assert r.new_groups == [] and r.gone == []
    assert g.legs[0].qty == 10


def test_side_flip_closes_old_leg_and_opens_new():
    g = _run([], [
        {"product_id": 1, "product_symbol": "C-BTC-105000-260926", "size": -10, "entry_price": "120"},
    ]).new_groups[0]
    r = _run([g], [
        {"product_id": 1, "product_symbol": "C-BTC-105000-260926", "size": 5, "entry_price": "90"},
    ])
    assert [lg.side for _, lg in r.gone] == ["sell"]
    assert [(lg.side, lg.status) for lg in g.legs] == [("sell", "open"), ("buy", "open")]
