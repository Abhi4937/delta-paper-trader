import uuid
from datetime import UTC, datetime

from app.live.sync import desired_bracket, evaluate_live, reconcile, usd_balance
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


def _group(stop_amount=None, sl=None, tp=None, target=None):
    """Short call (product 1) + long put (product 2), both entered at 100, 10 lots."""
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
    g.target_pnl = target
    g.target_pct_of_margin = None
    for lg in g.legs:
        lg.sl_price = (sl or {}).get(lg.product_id)
        lg.tp_price = (tp or {}).get(lg.product_id)
    return g


def _eval(g, marks, stale=False, grace=False):
    return evaluate_live(g, lambda lg: marks[lg.product_id], stale=stale, grace=grace).kind


def test_leg_sl_is_a_premium_trigger_independent_of_lots():
    g = _group(sl={1: 400})  # sold call at 100, SL when its premium reaches 400
    assert _eval(g, {1: 399, 2: 100}) == "none"
    assert _eval(g, {1: 400, 2: 100}) == "close-strategy"  # any leg SL closes ALL legs


def test_bought_leg_sl_fires_when_premium_falls():
    g = _group(sl={2: 60})
    assert _eval(g, {1: 100, 2: 61}) == "none"
    assert _eval(g, {1: 100, 2: 60}) == "close-strategy"


def test_leg_target_closes_whole_group():
    g = _group(tp={1: 40, 2: 180})  # sold call target 40, bought put target 180
    assert _eval(g, {1: 41, 2: 179}) == "none"
    assert _eval(g, {1: 40, 2: 100}) == "close-strategy"
    assert _eval(g, {1: 100, 2: 180}) == "close-strategy"


def test_basket_stop_and_target_close_whole_group():
    # net = -(call - 100)*0.01 + (put - 100)*0.01
    g = _group(stop_amount=1.0)
    assert _eval(g, {1: 180, 2: 70}) == "close-strategy"  # -0.8 -0.3 = -1.1 <= -1
    assert _eval(g, {1: 150, 2: 90}) == "none"  # -0.6
    g = _group(target=0.5)
    assert _eval(g, {1: 60, 2: 120}) == "close-strategy"  # +0.4 +0.2 = +0.6 >= 0.5
    assert _eval(g, {1: 80, 2: 110}) == "none"  # +0.3


def test_stale_feed_suspends_then_enforces_stops_but_never_targets():
    g = _group(stop_amount=1.0, target=0.5)
    assert _eval(g, {1: 180, 2: 70}, stale=True) == "suspended"
    assert _eval(g, {1: 180, 2: 70}, stale=True, grace=True) == "close-strategy"  # hard stop
    assert _eval(g, {1: 60, 2: 120}, stale=True, grace=True) == "none"  # no target on stale


def test_bracket_prices_from_leg_triggers_and_basket():
    g = _group(stop_amount=1.0, sl={1: 400}, tp={1: 40})
    short = next(lg for lg in g.legs if lg.product_id == 1)
    long_ = next(lg for lg in g.legs if lg.product_id == 2)
    assert desired_bracket(g, short, 0.5) == (200, 40)  # basket ($1 over 10 lots) tighter than 400
    assert desired_bracket(g, long_, 0.5) == (None, None)  # long max loss is its $1 premium


def test_usd_balance():
    assert (
        usd_balance(
            [{"asset_symbol": "BTC", "balance": "1"}, {"asset_symbol": "USD", "balance": "250.5"}]
        )
        == 250.5
    )
    assert usd_balance([]) is None


def test_feed_gap_never_closes_a_leg_delta_still_holds():
    g = _run(
        [],
        [
            {
                "product_id": 1,
                "product_symbol": "C-BTC-105000-260926",
                "size": -10,
                "entry_price": "120",
            },
        ],
    ).new_groups[0]
    # the feed momentarily can't describe the symbol: leg must stay open, no alert
    r = reconcile(
        UID,
        [g],
        [
            {
                "product_id": 1,
                "product_symbol": "C-BTC-105000-260926",
                "size": -10,
                "entry_price": "120",
            },
        ],
        lambda sym: None,
        lambda u: 0.0,
        NOW,
    )
    assert r.gone == [] and r.untracked == [] and r.new_groups == []


def test_exiting_group_never_absorbs_a_reopened_position():
    g = _run(
        [],
        [
            {
                "product_id": 1,
                "product_symbol": "C-BTC-105000-260926",
                "size": -10,
                "entry_price": "120",
            },
        ],
    ).new_groups[0]
    g.exiting = True
    old = g.legs[0]
    old.status = "closed"  # the exit closed it
    r = _run(
        [g],
        [
            {
                "product_id": 1,
                "product_symbol": "C-BTC-105000-260926",
                "size": -10,
                "entry_price": "130",
            },
        ],
    )
    assert len(r.new_groups) == 1 and r.new_groups[0] is not g
    assert r.new_groups[0].auto_exit is False
    assert len(g.legs) == 1


def test_exiting_group_legs_are_not_resized_mid_exit():
    g = _run(
        [],
        [
            {
                "product_id": 1,
                "product_symbol": "C-BTC-105000-260926",
                "size": -10,
                "entry_price": "120",
            },
        ],
    ).new_groups[0]
    g.exiting = True
    r = _run(
        [g],
        [
            {
                "product_id": 1,
                "product_symbol": "C-BTC-105000-260926",
                "size": -4,
                "entry_price": "120",
            },
        ],
    )
    assert r.new_groups == [] and r.gone == []
    assert g.legs[0].qty == 10


def test_side_flip_closes_old_leg_and_opens_new():
    g = _run(
        [],
        [
            {
                "product_id": 1,
                "product_symbol": "C-BTC-105000-260926",
                "size": -10,
                "entry_price": "120",
            },
        ],
    ).new_groups[0]
    r = _run(
        [g],
        [
            {
                "product_id": 1,
                "product_symbol": "C-BTC-105000-260926",
                "size": 5,
                "entry_price": "90",
            },
        ],
    )
    assert [lg.side for _, lg in r.gone] == ["sell"]
    assert [(lg.side, lg.status) for lg in g.legs] == [("sell", "open"), ("buy", "open")]


def test_legs_have_a_fixed_order():
    # without it an UPDATE moves a live leg to the end and rows swap on screen mid-edit
    from app.db.models import Position

    assert Position.legs.property.order_by, "Position.legs must be ordered"


async def test_bracket_sync_leaves_a_leg_delta_reports_flat_alone():
    """Race: Delta's bracket fired between syncs. Re-placing a bracket there would orphan the
    fill's order id, so the cascade to the other legs would never happen."""
    from types import SimpleNamespace

    from app.live.sync import LiveSync

    g = _group(sl={1: 400, 2: 60})  # both legs have an SL: the open one must get its bracket
    g.auto_exit, g.exiting = True, False
    short = next(lg for lg in g.legs if lg.product_id == 1)
    short.stop_order_id, short.tp_order_id = 777, None
    placed = []

    class Client:
        async def open_stop_orders(self):
            return []  # our bracket is gone (it fired)

        async def positions(self):
            return [{"product_id": 1, "size": 0}, {"product_id": 2, "size": 10}]

        async def place_bracket(self, *a, **k):
            placed.append(a)
            return "market"

        async def cancel(self, *a):
            pass

    app = SimpleNamespace(state=SimpleNamespace(market=SimpleNamespace(tickers={})))
    sync = LiveSync(app)
    session = SimpleNamespace(add=lambda _row: None)
    await sync.sync_brackets(session, UID, Client(), [g])
    assert [a[0] for a in placed] == [2]  # only the still-open leg; never the flat one
    assert short.stop_order_id == 777  # kept, so the reconcile can match the fill
