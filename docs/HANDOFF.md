# Session Handoff — Delta Options Paper-Trading Platform

_Last updated: 2026-06-09_

> **New session: read `docs/ARCHITECTURE.md` first** (system map + API contracts + data model + engines) — it replaces reading the codebase cold. Then this file for run commands & status.

## TL;DR
Local paper-trading terminal for **Delta Exchange India options** (BTC + ETH), mirroring Delta/Sensibull. **PAPER-ONLY — read-only key, no order ever placed.** v1 core loop (chain → builder → paper order → positions → live MTM) is **functional end-to-end**. Money model (fees/slippage/MTM) is **validated against a real Delta account at 10 lots**. Currently in-memory only (no DB persistence yet).

---

## How to run
```powershell
# Backend (FastAPI, port 8010) — NO --reload, so restart after every backend edit
cd C:\dev\paper_trader\backend
uv run uvicorn app.main:app --port 8010      # (running as background task during dev)

# Frontend (Next.js, port 3000)
cd C:\dev\paper_trader\frontend
npm run dev
```
- Backend `.env` holds the **read-only** Delta key (server-side only).
- **Every backend code change requires a manual restart** (uvicorn runs without `--reload`).
- Hard nav / `page.goto` / hot-reload **resets the Zustand store** (positions are in-memory). Navigate via in-app `<Link>` to preserve state.

---

## Architecture (current)
- **Backend** `backend/app/`
  - `services/market_data.py` — `MarketDataIngestor`: persistent Delta WS, in-memory `tickers` cache. Subscribes to **both** `v2/ticker` (5s snapshot: greeks/IV/OI) **and** `mark_price` (continuous live mark, `MARK:` prefix). `_live_mark` field preserves the fast mark across 5s ticker overwrites. Auto-reconnect.
  - `main.py` — endpoints: `/ws/chain` (0.3s push from cache), `/api/marks` (mark/bid/ask/iv **+ greeks** for held symbols), `/api/atm-iv` (ATM IV per expiry, for the analytics IV panel), `/api/orderbook` (L2), `/api/margin`, `/api/chain/expiries`.
  - `services/chain.py` — `build_chain` / `normalize_contract`; Contract exposes `last_price` (LTP), `oi_value_usd`.
- **Frontend** `frontend/src/`
  - `lib/store.ts` — Zustand singleton. **Money model lives here** (see below). marks(+greeks)/spots/atmIvs maps, legs, positions, ledger, per-position **series** sampling (net + per-leg PnL/IV/greeks, 1/sec **from open**, cap `MTM_CAP`=12h), `startStream()` → `pollPositionMarks` (1.5s, also fetches ATM IV). Net greek = Σ(signed_qty×cv×greek); **standard aggregation — validate absolute greeks vs a real Delta account.**
  - `lib/api.ts` — `connectChain` (WS), `fetchMarks`, `fetchOrderbook`, `fetchMargin`, mappers.
  - `app/(terminal)/chain/page.tsx` — lean 11-col chain (θ·IV·Δ·Mark·OI | STRIKE | OI·Mark·Δ·IV·θ), ATM line + auto-center, B/S on hover (builder mode only), USD-notional OI ($M/$K).
  - `components/StrategyBuilder.tsx` — toggle panel (OrderBook XOR Builder); B/S add legs; place → `router.push("/positions")`, does NOT clear basket.
  - `components/OrderBook.tsx` — L2 panel, follows clicked strike.
  - `app/(terminal)/positions/page.tsx` — isolated strategy cards, Delta-style columns, `PositionCharts`.
  - `components/PositionCharts.tsx` — stacked analytics (lightweight-charts v5): **MTM** (net baseline green/red or candle + per-leg), **IV** (a bold ATM line **per expiry** + per-leg IVs), **Delta** (net + per-leg), **Theta** (net), **Vega** (net). Shared timeframe **1s/1m/5m** (default 1s) + **Line/Candle** (MTM only). Leg colors grouped by **expiry hue** (CE lighter / PE deeper), same color for a leg across all panels; multi-line crosshair tooltip (date·time + net + each leg, MTM/Δ values green/red by sign). Per-panel **collapse** (Θ/Vega shorter; collapsing grows the rest); time/value axes on every panel; solid 0-line. Charts auto-fit start→latest (Y autoscales, 0-line always shown). Aggregates the per-second `series` into OHLC/line buckets.
  - `app/(terminal)/positions/page.tsx` cards: each **strategy collapses** (closed → collapsed by default); **Close** button in the header; legs table has an **Entry Time** column.

---

## Money model (VALIDATED — do not change without re-validating)
Memory: `money-model-validated.md` — confirmed vs real Delta account @10 lots. Real acct = **Options Carnival** rate; demo = **Standard**.
- **1 lot = 0.001 BTC** (`contract_value`). Everything in **USD**; `$/₹` display toggle at **fixed 1 USD = 85 INR**.
- **Fee (offer-aware):** `min(0.010% notional, 3.5% premium) × (1+18% GST)` = Options Carnival (`ACTIVE_FEE`). Standard = 0.03%/10%. Notional = spot×cv×qty; premium = price×cv×qty. Referral discount = 0 (not eligible).
- **Slippage:** top-of-book fill (buy=ask, sell=bid); book deep enough that ≤100 lots fill at top. Entry slip = Σ|entry−markAtEntry|×qty×cv.
- **MTM = entry-slippage only, NO fees.** Close = gross (with exit slippage) − entry fee − exit fee. Positions header shows Margin · Entry slip · Entry fee separately.

---

## Realtime PnL — RESOLVED
**Root cause of the "5s lag":** ingestor only read the mark from `v2/ticker` (5s snapshot). **Fix:** also subscribe to `mark_price` (continuous). Verified in UI — leg marks/PnL now tick ~every 1–2s, matching Delta.
**Doc verification (this session):** Delta docs **confirm** `mark_price` is a separate "continuously updated" channel with the `MARK:` prefix, and that `ticker` carries greeks/IV/OI/quotes. Docs do **not** publish exact second-counts — the "5s / ~2s" figures are **empirical** (measured from the stream), not documented. Architecture is doc-consistent; no code change needed.

---

## Pending work (priority order)
1. ✅ **Per-leg TP/SL + per-leg close + combined SL** — DONE. Pure decision engine `lib/exit.ts` (vitest); per-leg TP/SL on leg PnL with a close-leg-only/whole-strategy toggle; combined SL = ₹/$ amount or % of margin; **auto-exit suspended when a leg's mark is >10s stale**. `closeLeg` recomputes exact margin (`/api/margin`) for the remaining legs. UI: `RiskPanel` on the positions card + "auto-exit paused · stale" badge.
2. ✅ **Analyse Payoff** (task #9) — DONE. `components/PayoffPanel.tsx` (Sensibull-style, tabbed: Payoff / P&L Table / Greeks). Chart: On-Expiry + On-Target-Date curves, Call/Put **OI bars**, **±1σ/±2σ SD bands**, current+target markers, projected-profit pill. Controls: target-spot/DTE/IV **stepper+slider** (−/＋), a **date-time picker** (DTE), **strikewise IV** (per-leg + global offset), now·live vs what-if. Greeks tab: net + **per-leg greeks**, Position/Per-lot toggle, target-day SD table. Each leg's vol is **calibrated to its live mark** (`impliedVol` in `lib/bs.ts`) so projected-at-now == real entry slippage (verified vs a placed position). Backend `POST /api/payoff` (Black-Scholes r=0 via `engines/blackscholes.py` + `engines/payoff.py` `projected_pnl`/`net_greeks`); one debounced call per slider settle.
3. **Analytics / Notes / Logs** screens (task #10).
4. **DB schema + persistence + order/position loop** (task #11) — positions currently in-memory, lost on hard refresh. Postgres + TimescaleDB per plan.
5. **Auth, hosting, Figma import** (task #12).
- _Optional:_ wire **`l1_orderbook`** for sub-second bid/ask on the chain (docs confirm it's the realtime best-bid/ask channel).
- _Other Delta position tabs noted:_ Open Orders, Stop Orders, Risk & Margin, Fills, Order History.

---

## Stale-data guard (never trade on frozen prices)
- Backend `/api/feed` → `{connected, age_seconds, fresh}` from the WS ingestor (`last_message_at`); `fresh=false` if the Delta WS is down OR no message in >5s. The store polls it every 2s (`feedFresh`/`feedAge`).
- When stale: a top **banner** shows, the top-bar indicator reads **"stale"** (warn), and **order placement is blocked** in the builder (the entry-side of "never act on bad data"). The `live`/`down` badge tracks the localhost socket; **`stale` tracks the actual Delta feed**.
- Why it exists: during an outage the local backend's Delta data freezes while localhost stays "live" — placing then fills at stale prices. See the per-leg risk guard (auto-exit also suspends on stale marks).

## Key constraints / gotchas
- **PAPER-ONLY, NEVER LIVE.** Only the read-only key is ever used. No place/modify/cancel path. Keys server-side only. (Assistant has declined real/demo account credentials — keep declining.)
- **Ask, don't assume** on anything affecting money/risk/correctness (CLAUDE.md golden rule #1).
- **No mock data in shipped features** — every number traces to a real endpoint or tested calc.
- 0-DTE options have no L2 book (orderbook 404s are normal for those).
- Backend has **no `--reload`** → restart after edits.
- Plan file: `~/.claude/plans/i-want-to-create-prancy-sloth.md`. Checklist: `software-development-master-checklist.md`.
