"""QA A7: a genuinely-zero mark is a real total loss, not "no quote".

A long option that decays to 0 must show its full premium loss (so a max-loss stop can
fire); only a MISSING mark falls back to the entry price.
"""
import uuid
from types import SimpleNamespace

from app.live.risk import ioc_limit_price
from app.live.sync import evaluate_live
from app.sim import service
from app.sim.exit_engine import CombinedStop, evaluate_exit
from app.sim.marketview import MarketView


class FakeIngestor:
    def __init__(self, ticker):
        self.tickers = {"C-BTC-90000-260926": ticker}


def _mv(ticker) -> MarketView:
    return MarketView(FakeIngestor(ticker))  # type: ignore[arg-type]


def _long(entry=50.0):
    return SimpleNamespace(
        id=uuid.uuid4(), symbol="C-BTC-90000-260926", side="buy", qty=10,
        contract_value=0.001, entry=entry, status="open", stop_pnl=None,
    )


def test_quote_keeps_missing_and_zero_marks_apart():
    assert _mv({"mark_price": "0"}).quote("C-BTC-90000-260926").mark == 0.0
    assert _mv({"symbol": "C-BTC-90000-260926"}).quote("C-BTC-90000-260926").mark is None
    assert _mv({"mark_price": ""}).quote("C-BTC-90000-260926").mark is None


def test_worthless_long_shows_full_premium_loss():
    leg = _long(entry=50.0)
    pos = SimpleNamespace(legs=[leg])
    # 10 contracts x 0.001 x 50 premium = 0.5 USD, all lost
    assert service.position_pnl(pos, _mv({"mark_price": "0"})) == -0.5
    # no mark at all: unknown, so no fake loss
    assert service.position_pnl(pos, _mv({"symbol": "C-BTC-90000-260926"})) == 0.0


def test_max_loss_stop_fires_on_a_worthless_long():
    leg = _long(entry=50.0)
    pos = SimpleNamespace(legs=[leg], margin=1.0)
    mv = _mv({"mark_price": "0"})
    d = evaluate_exit(
        service.exit_legs_of(SimpleNamespace(legs=[SimpleNamespace(
            **vars(leg), target_pnl=None, auto_exit=True, close_scope="strategy")]), mv),
        net_pnl=service.position_pnl(pos, mv), margin=1.0,
        combined_stop=CombinedStop(0.4, None), combined_auto_exit=True, stale=False,
    )
    assert d.kind == "close-strategy"


def test_live_basket_stop_fires_on_a_worthless_long():
    leg = _long(entry=50.0)
    g = SimpleNamespace(legs=[leg], margin=1.0, stop_loss_amount=0.4, stop_loss_pct_of_margin=None)
    assert evaluate_live(g, lambda lg: 0.0, stale=False, grace=False).kind == "close-strategy"


def test_buy_back_at_zero_mark_is_still_priced_to_fill():
    assert ioc_limit_price("buy", 0.0, 0.05, 0.5) >= 0.5
