"""Capture Delta's exact margin for a batch of baskets + compare to the local model.

Pulls ground truth from the live estimator (needs DELTA_WEB_JWT) and runs the
local portfolio-margin engine on the same baskets, saving a calibration dataset
and printing the divergence. Re-runnable for continuous calibration (ADR 0002).

Run: uv run python -m scripts.capture_margin_calibration
"""

from __future__ import annotations

import asyncio
import json
from datetime import date

import httpx

from app.config import get_settings
from app.engines.margin import MarginLeg, compute_margin, implied_vol
from app.services import chain as cs
from app.services.chain import Contract, OptionChain

s = get_settings()
EST_URL = f"{s.delta_web_base}/v2/orders/estimate_margin/basket"
HEADERS = {
    "authorization": s.delta_web_jwt,
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0",
}


async def estimate(h: httpx.AsyncClient, orders: list[dict]) -> dict:
    payload = {"index_symbol": ".DEXBTUSD", "orders": orders, "source": "desktop"}
    r = await h.post(EST_URL, json=payload, headers=HEADERS)
    r.raise_for_status()
    return r.json()["result"]


def order(c: Contract, side: str, size: int) -> dict:
    return {
        "product_id": c.product_id,
        "side": side,
        "size": size,
        "order_type": "market_order",
        "time_in_force": "gtc",
    }


def mleg(c: Contract, side: str, size: int, dte_days: int, spot: float) -> MarginLeg:
    # anchor IV to the mark (removes BS-vs-mark basis); fall back to reported IV
    t = dte_days / 365.0
    iv = implied_vol(c.option_type, spot, c.strike, t, c.mark_price or 0.0)
    if iv <= 0:
        iv = c.quote.mark_iv or 0.0
    return MarginLeg(
        option_type=c.option_type,
        side="long" if side == "buy" else "short",
        qty=size,
        strike=c.strike,
        dte_days=dte_days,
        iv=iv,
        contract_value=c.contract_value or 0.001,
        mark=c.mark_price or 0.0,
    )


def near(chain: OptionChain, target: float, opt: str) -> Contract:
    rows = [r for r in chain.rows if (r.call if opt == "call" else r.put)]
    row = min(rows, key=lambda r: abs(r.strike - target))
    return row.call if opt == "call" else row.put  # type: ignore[return-value]


async def main() -> None:
    async with cs.DeltaRestClient() as c:
        tickers = await cs.fetch_option_tickers(c, "BTC")
        expiries = cs.list_expiries(tickers)
        today = date.today()
        # pick an expiry ~7 days out for meaningful time value
        target_exp = min(expiries, key=lambda e: abs((e - today).days - 7))
        chain = cs.build_chain(tickers, "BTC", target_exp)
        spot = chain.spot or 0.0
        dte = max((target_exp - today).days, 1)
        print(f"expiry={target_exp} dte={dte} spot={spot} atm={chain.atm_strike}")

        atm_c = near(chain, spot, "call")
        atm_p = near(chain, spot, "put")
        otm_c = near(chain, spot * 1.03, "call")
        otm_p = near(chain, spot * 0.97, "put")
        far_c = near(chain, spot * 1.06, "call")
        far_p = near(chain, spot * 0.94, "put")

        baskets: list[tuple[str, list[tuple[Contract, str, int]]]] = [
            ("short_atm_call", [(atm_c, "sell", 1)]),
            ("long_atm_call", [(atm_c, "buy", 1)]),
            ("short_atm_put", [(atm_p, "sell", 1)]),
            ("short_straddle", [(atm_c, "sell", 1), (atm_p, "sell", 1)]),
            ("long_straddle", [(atm_c, "buy", 1), (atm_p, "buy", 1)]),
            ("short_strangle", [(otm_c, "sell", 1), (otm_p, "sell", 1)]),
            ("bull_call_spread", [(atm_c, "buy", 1), (otm_c, "sell", 1)]),
            ("iron_condor", [
                (otm_c, "sell", 1), (far_c, "buy", 1),
                (otm_p, "sell", 1), (far_p, "buy", 1),
            ]),
            ("short_call_x10", [(atm_c, "sell", 10)]),
        ]

        out = []
        async with httpx.AsyncClient(timeout=20) as h:
            for name, legs in baskets:
                try:
                    res = await estimate(h, [order(ct, sd, sz) for ct, sd, sz in legs])
                    delta_m = float(res["portfolio_margin"])
                except Exception as e:  # noqa: BLE001
                    print(f"{name:18s} ESTIMATOR ERR {type(e).__name__} {str(e)[:80]}")
                    continue
                local = compute_margin(
                    [mleg(ct, sd, sz, dte, spot) for ct, sd, sz in legs], spot
                ).initial_margin
                ratio = local / delta_m if delta_m else float("nan")
                print(f"{name:18s} delta={delta_m:10.4f}  local={local:10.4f}  "
                      f"local/delta={ratio:6.2f}")
                out.append({
                    "name": name,
                    "spot": spot,
                    "dte_days": dte,
                    "delta_margin": delta_m,
                    "local_margin": local,
                    "legs": [
                        {"symbol": ct.symbol, "product_id": ct.product_id,
                         "option_type": ct.option_type, "side": sd, "size": sz,
                         "strike": ct.strike, "mark": ct.mark_price,
                         "iv": ct.quote.mark_iv, "contract_value": ct.contract_value}
                        for ct, sd, sz in legs
                    ],
                })

        path = "tests/fixtures/margin_calibration_btc.json"
        import os
        os.makedirs("tests/fixtures", exist_ok=True)
        with open(path, "w") as f:  # noqa: ASYNC230  (one-off capture script)
            json.dump({"captured_dte": dte, "spot": spot, "baskets": out}, f, indent=2)
        print(f"\nsaved {len(out)} baskets -> {path}")


if __name__ == "__main__":
    asyncio.run(main())
