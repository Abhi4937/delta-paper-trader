"""Broad strategy calibration: local margin model vs live Delta estimator.

Tests verticals (bull/bear call/put), calendars, diagonals, double calendar,
risk reversal, straddles/strangles — tagged by net credit/debit and net vega.
Needs DELTA_WEB_JWT. Run: uv run python -m scripts.calibrate_strategies
"""

from __future__ import annotations

import asyncio
from datetime import date

import httpx

from app.config import get_settings
from app.engines.margin import MarginLeg, compute_margin, implied_vol
from app.services import chain as cs
from app.services.chain import Contract, OptionChain

s = get_settings()
EST_URL = f"{s.delta_web_base}/v2/orders/estimate_margin/basket"
HEADERS = {"authorization": s.delta_web_jwt, "Content-Type": "application/json",
           "User-Agent": "Mozilla/5.0"}

# A strategy leg: (contract, side 'buy'/'sell', size, dte_days)
Leg = tuple[Contract, str, int, int]


async def estimate(h: httpx.AsyncClient, legs: list[Leg]) -> float:
    orders = [{"product_id": c.product_id, "side": sd, "size": sz,
               "order_type": "market_order", "time_in_force": "gtc"}
              for c, sd, sz, _ in legs]
    r = await h.post(EST_URL, json={"index_symbol": ".DEXBTUSD", "orders": orders,
                                    "source": "desktop"}, headers=HEADERS)
    r.raise_for_status()
    return float(r.json()["result"]["portfolio_margin"])


def mleg(c: Contract, side: str, size: int, dte: int, spot: float) -> MarginLeg:
    iv = implied_vol(c.option_type, spot, c.strike, dte / 365.0, c.mark_price or 0.0)
    if iv <= 0:
        iv = c.quote.mark_iv or 0.0
    return MarginLeg(c.option_type, "long" if side == "buy" else "short", size,
                     c.strike, dte, iv, c.contract_value or 0.001, c.mark_price or 0.0)


def near(chain: OptionChain, target: float, opt: str) -> Contract:
    rows = [r for r in chain.rows if (r.call if opt == "call" else r.put)]
    row = min(rows, key=lambda r: abs(r.strike - target))
    return row.call if opt == "call" else row.put  # type: ignore[return-value]


def net_cash(legs: list[Leg]) -> float:
    # + = credit received, - = debit paid
    return sum((1 if sd == "sell" else -1) * sz * (c.contract_value or 0.001)
               * (c.mark_price or 0.0) for c, sd, sz, _ in legs)


def net_vega(legs: list[Leg]) -> float:
    return sum((1 if sd == "buy" else -1) * sz * (c.greeks.vega or 0.0)
               for c, sd, sz, _ in legs)


async def main() -> None:
    async with cs.DeltaRestClient() as c:
        tickers = await cs.fetch_option_tickers(c, "BTC")
        expiries = cs.list_expiries(tickers)
        today = date.today()
        near_exp = min(expiries, key=lambda e: abs((e - today).days - 7))
        far_exp = min(expiries, key=lambda e: abs((e - today).days - 28))
        nd = max((near_exp - today).days, 1)
        fd = max((far_exp - today).days, 1)
        nc = cs.build_chain(tickers, "BTC", near_exp)
        fc = cs.build_chain(tickers, "BTC", far_exp)
        spot = nc.spot or 0.0
        print(f"spot={spot:.0f}  near={near_exp}({nd}d)  far={far_exp}({fd}d)")

        # near-expiry contracts
        ac, ap = near(nc, spot, "call"), near(nc, spot, "put")
        oc, op = near(nc, spot * 1.03, "call"), near(nc, spot * 0.97, "put")
        # far-expiry contracts
        fac, fap = near(fc, spot, "call"), near(fc, spot, "put")
        wc, wp = near(nc, spot * 1.06, "call"), near(nc, spot * 0.94, "put")

        strategies: dict[str, list[Leg]] = {
            "bull_call (debit)":  [(ac, "buy", 1, nd), (oc, "sell", 1, nd)],
            "bear_call (credit)": [(ac, "sell", 1, nd), (oc, "buy", 1, nd)],
            "bull_put (credit)":  [(ap, "sell", 1, nd), (op, "buy", 1, nd)],
            "bear_put (debit)":   [(ap, "buy", 1, nd), (op, "sell", 1, nd)],
            "long_straddle":      [(ac, "buy", 1, nd), (ap, "buy", 1, nd)],
            "short_straddle":     [(ac, "sell", 1, nd), (ap, "sell", 1, nd)],
            "risk_reversal":      [(op, "sell", 1, nd), (oc, "buy", 1, nd)],
            "call_calendar":      [(ac, "sell", 1, nd), (fac, "buy", 1, fd)],
            "put_calendar":       [(ap, "sell", 1, nd), (fap, "buy", 1, fd)],
            "double_calendar":    [(ac, "sell", 1, nd), (fac, "buy", 1, fd),
                                   (ap, "sell", 1, nd), (fap, "buy", 1, fd)],
            "call_diagonal":      [(oc, "sell", 1, nd), (fac, "buy", 1, fd)],
            "iron_condor":        [(oc, "sell", 1, nd), (wc, "buy", 1, nd),
                                   (op, "sell", 1, nd), (wp, "buy", 1, nd)],
        }

        print(f"{'strategy':20s} {'delta':>9s} {'local':>9s} {'ratio':>6s}  "
              f"{'cash':>7s} {'vega':>8s}")
        async with httpx.AsyncClient(timeout=20) as h:
            for name, legs in strategies.items():
                try:
                    delta_m = await estimate(h, legs)
                except Exception as e:  # noqa: BLE001
                    print(f"{name:20s} ESTIMATOR ERR {type(e).__name__} {str(e)[:60]}")
                    continue
                local = compute_margin(
                    [mleg(c, sd, sz, dte, spot) for c, sd, sz, dte in legs], spot
                ).initial_margin
                ratio = local / delta_m if delta_m else float("nan")
                cash = net_cash(legs)
                vega = net_vega(legs)
                tag = "cr" if cash > 0 else "db"
                vtag = "Lv" if vega > 0 else "Sv"
                print(f"{name:20s} {delta_m:9.4f} {local:9.4f} {ratio:6.2f}  "
                      f"{cash:6.3f}{tag} {vega:7.2f}{vtag}")


if __name__ == "__main__":
    asyncio.run(main())
