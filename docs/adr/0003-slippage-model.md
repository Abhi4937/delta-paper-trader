# ADR 0003 — Slippage / paper-fill model

**Status:** Accepted (2026-06-09)
**Context:** Paper fills must be realistic. Delta's own mark price is an **orderbook impact-price** (VWAP to a reference "impact size"), and execution is **capped by Allowed Trading Bands** with **IOC partial fills** on market orders. We model fills the same way, and capture microstructure to train a better model later.

Sources: Delta India guides — *Fair Price Marking*, *Allowed Trading Bands*, *Order Types*.

## Mechanics (confirmed)
- **Market order** = walks best levels; **default IOC** → unfilled portion (beyond available in-band depth) is **cancelled** (partial fills are real).
- **Limit** = fills at limit-or-better, default GTC. **Post-only** = maker-only, rejected if it would cross.
- **Mark price (options)** = orderbook impact bid/ask averaged to impact size, then **capped to BS(model IV ± 25%)**. Updated ~5s. (We use Delta's `mark_price` directly for MTM.)
- **Allowed Trading Bands** cap execution to the widest of: ±2σ(15m), BS(mid IV ± IV-range), ±(spot×range%). Per-contract band is on the ticker `price_band`. **Market orders breaching the band → IOC cancels the excess.**

## Model (`app/engines/slippage.py`)
1. **Orderbook-walk VWAP** of live L2 (`l2_orderbook` WS / `/v2/l2orderbook`) for the actual order size: buy walks asks ascending, sell walks bids descending. `Decimal` only — floats never touch a fill price.
2. **Trading-band cap + partial fill:** stop consuming at the band boundary; remainder is **unfilled** (IOC). Report `filled_qty` < `requested_qty` when in-band depth is short.
3. **Optional linear impact** overlay (ported baseline): `impact = vwap·k·notional/vol_24h`, illiquid floor — but `k` is a *seed* to be replaced by the trained model.
4. **Thin/empty-book fallback:** `mark ± (spread/2 + slippage_ticks·tick_size)`, clamped to the band, **flagged** (excluded from training).
5. **Capture per fill** (the training dataset): requested/filled qty, side, ts; top-N L2 ladder; best bid/ask, spread; sizes; mark/spot; iv; greeks; oi; band; `slippage_vs_mark_bps`/`slippage_vs_mid_bps` (labels); fallback flag.

## Decision
Ship the orderbook-walk + **band-cap + IOC partial-fill** + fallback now (this is the Delta-specific upgrade over the old project's plain walk+linear-`k`). Capture every fill; train a data-driven impact model later to replace `k`. Use Delta `mark_price` for MTM.

## Consequences
- Needs live L2 (freshness is the real constraint; option books are thin/time-varying).
- Band data per contract comes from the ticker `price_band` — verify field shape against live data when wiring.
