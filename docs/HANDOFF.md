# Session Handoff — Delta Options Paper-Trading Platform

_Last updated: 2026-06-10_

> **New session: read `docs/ARCHITECTURE.md` first** (system map + API contracts + data model + engines) — it replaces reading the codebase cold. Then this file for run commands & status.
>
> **✅ "Real save" (server-side persistence) is COMPLETE — all 4 phases done. The frontend is now server-authoritative (hydrates from GET /api/state + WS tick). See "REAL SAVE" below for the final architecture.**

## TL;DR
Local paper-trading terminal for **Delta Exchange India options** (BTC + ETH), mirroring Delta/Sensibull. **PAPER-ONLY — read-only key, no order ever placed.** v1 core loop (chain → builder → paper order → positions → live MTM) is **functional end-to-end**, plus Analyse Payoff, Analytics/Notes/Logs screens, Excel export, and Analyse-on-position. Money model (fees/slippage/MTM) is **validated against a real Delta account at 10 lots**.

**State today:** the app is now **server-authoritative** — the backend (`uv run uvicorn app.main:app --port 8010`) owns positions/balance/ledger/logs in Postgres+Timescale; the frontend hydrates from `GET /api/state` and live-ticks over `WS /api/ws/state`. localStorage now persists only UI prefs (basket/underlying/expiry/currency). **A stale dockerized backend runs on :8000 (no sim routes) — ignore it; the frontend targets :8010.**

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

```powershell
# DB infra (Postgres+TimescaleDB + Redis) — REQUIRED for the real-save work
docker compose up -d                               # from repo root; needs Docker Desktop running
# Docker Desktop launch (if engine is down): Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"
cd C:\dev\paper_trader\backend
uv run alembic upgrade head                         # apply migrations (already applied this session)
uv run alembic revision --autogenerate -m "msg"     # after model changes (then hand-edit for Timescale ops)
# psql:  docker exec paper_trader_db psql -U paper -d paper_trader -c "\dt"
```
- DB defaults (config.py): `postgresql+asyncpg://paper:paper@localhost:5432/paper_trader`, `redis://localhost:6379/0` — match docker-compose, no `.env` needed for DB.

---

## 🚧 REAL SAVE — server-side persistence (IN PROGRESS, resume here)

Goal: move the simulation from client-side Zustand to a **server-authoritative engine** with durable Postgres+Timescale storage, so state is multi-user, cross-device, and auto-exit runs 24/7.

### Decisions (locked with the user — do not re-litigate)
1. **Full server-side engine** (place/fill/MTM/exit all in Python; server is the source of truth). NOT a thin sync layer.
2. **Stub user now, isolation-ready schema** — every table has `user_id`; one seeded trusted user for now; real auth is a later phase.
3. **1-min downsampled series into a Timescale hypertable** (`strategy_series`). Live per-second ticks stay client-side; the 1-min rollup is the durable history.
4. **Portfolio only** — no option-chain history table (chain stays live-only from Delta).
5. **Continuous margin + slippage**: the tick loop re-fetches **exact margin** (Delta read-only SB endpoint) as marks move (store entry_margin + rolling margin; badge matched/est/stale), and exposes a **live exit/spread cost** (entry slippage stays the fixed historical entry cost).
6. **Capture bid/ask/spread** per leg in the 1-min series (`legs` JSONB = `{leg_id: {pnl,iv,delta,bid,ask}}`; spread = ask−bid). Surface best-bid/best-ask/spread in positions + chain.

### DONE (Phase 1 — committed `ba1e8f0`, `b03ea1f`)
- **`backend/app/engines/money.py`** — the validated client money model ported VERBATIM (FEE_SCHEDULES/ACTIVE_FEE = Options Carnival, `leg_fee`, `entry_fill`/`exit_fill`, `leg_entry_slippage`, `leg_pnl`/`net_pnl` (LegQuote), `net_greeks` (LegGreeks), `spread`). **`tests/test_money.py` — 12 golden tests pass** (locked to Delta's calculator; same formulas → preserves `money-model-validated`). This is the authoritative server money model. (Pre-existing `engines/pnl.py`/`slippage.py` are separate/abstract — money.py is the one to use.)
- **`backend/app/db/`** — `base.py` (DeclarativeBase), `session.py` (async engine + `get_session` dep, commits on success), `models.py`: **users, accounts, positions, legs, ledger_entries, logs, notes, strategy_series** — all `user_id`-isolated, mypy-strict clean. Field names mirror `frontend/src/lib/types.ts` (snake_case). `positions` has `margin` (rolling) + `entry_margin`. `strategy_series` PK `(time, position_id)`, JSONB `atm_iv` + `legs`.
- **Alembic** (async) wired to `app.config` + `Base.metadata` (`migrations/env.py`). Migration `df58c58a8bf9` applied; **`strategy_series` is a live TimescaleDB hypertable** (verified). Docker DB+Redis running.

### DONE (Phase 2 + 3 — committed `de0d3de`; VERIFIED end-to-end vs live DB + Delta feed)
Server is now authoritative. `backend/app/sim/`:
- **`marketview.py`** — read-only adapter: `quote(symbol)` (mark/bid/ask/iv/greeks), `spot(underlying)` (ticker `spot_price`), `atm_iv`, `fresh()` (whole-feed staleness).
- **`exit_engine.py`** — `evaluate_exit(...)` ported from exit.ts (stale→suspend, combined SL, per-leg TP/SL w/ leg|strategy scope).
- **`service.py`** — `place_strategy` / `close_position` / `close_leg` / `set_position_risk` / `set_leg_risk` / `add_note`; balance math ported verbatim from store.ts; uses `engines/money.py`. Plus `build_sample` (per-leg pnl/iv/delta/**bid/ask**), `position_dict`/`position_live_dict`/`get_state` (camelCase, epoch-ms).
- **`ticker.py`** — 1s `SimTicker` (started in `main.py` lifespan): MTM + auto-exit 24/7, **in-memory per-second series ring** `app.state.sim_series` (keeps 1s charts), **1-min** rows → hypertable, exact-margin refresh /30s.
- **`user.py`** — stub user/account + opening deposit seed (`stub_user_email`, $5000). **`margin_helper.py`** — exact-margin quote reused by place/close-leg.
- **`api/sim.py`** — `GET /api/state`; `POST /api/strategies` (place), `POST /api/strategies/{id}/close`, `POST /api/strategies/{id}/legs/{leg_id}/close`, `PATCH /api/strategies/{id}/risk`, `PATCH /api/legs/{id}/risk`, `POST /api/strategies/{id}/notes`; **`WS /api/ws/state`** (1s tick: `{type:"tick", balance, currency, openIds, positions:[{…live fields…, sample}]}`). Every route resolves the stub user + filters by `user_id`. mypy-strict clean. ⚠️ only cosmetic `ruff E501` (line-length ×55 in sim/) left.
- **Verified:** placed a real short strangle → exact margin "matched" $1.087, entry@bid, bid/ask/spread saved; closed → exit@ask, realized/fee/margin_release ledger, balance settled. `curl localhost:8010/api/state` works.

### DONE (Phase 4 — frontend swap, `frontend/src/lib/`) — VERIFIED end-to-end in-browser
Server returns camelCase matching `types.ts` (legs carry live `mark`/`iv`/`bid`/`ask`/`spread`/`pnl`; positions carry live `pnl`/`delta`/`theta`/`vega`/`entryMargin`/`entrySlippage`; `Position.series` is `SeriesSample[]`). `types.ts` gained those as optional live fields.
- **`api.ts`** — added `fetchState()` (GET /api/state), `placeStrategy`, `closePositionApi`, `closeLegApi`, `setPositionRiskApi`, `setLegRiskApi`, `addNoteApi` (each returns the fresh full `ServerState`), `connectState()` (WS `/api/ws/state`). Risk PATCH bodies send only the keys present (server uses `model_fields_set` to distinguish "set null" from "leave").
- **`store.ts`** — rewritten server-backed: hydrate from `GET /api/state`; mutation actions call the API and apply the returned state; each WS tick merges live fields + **appends `position.sample` to its series** (≥1s) + sets balance; refetches full state when `openIds` diverges (server auto-exit OR a position placed elsewhere). Dropped the client marks-polling/auto-exit/sampling. **Kept** the chain WS (chain page + basket preview), display helpers (`positionPnl`→`p.pnl`, `legMark`→`l.mark`, `legIv`→`l.iv`), and **client-static** `entryFee`/`entrySlippage` (computed from the leg's static entry data — no live marks; mirrors the server money model, kept only for the entry-fee/slip line). Persist narrowed to UI prefs (basket/underlying/expiry/currency) with a **v2 migrate → clean start** (user chose clean start; old localStorage positions dropped, server DB untouched).
- **Builder live-premium fix** (`StrategyBuilder.tsx` + `PayoffPanel.tsx`): basket legs were frozen at add-time price. Now `legBasketPrice(leg)` (live would-fill from the chain feed: buy→ask, sell→bid) drives the builder's displayed premium + net credit/debit + the payoff "now" baseline, updating in real time until execute. The **real fill is set server-side at execute time** from the live quote (`entry_fill`), so the placed entry is no longer the stale add-time price.
- Stale guard: still uses `GET /api/feed` (unchanged) for the placement block + banner.
- **Verified:** `tsc --noEmit` + `eslint` clean (only pre-existing warnings); GET /api/state + WS tick shapes match the new types field-by-field; API place→live-tick(with `sample`)→close path correct; browser /positions hydrates server balance ($4,999.86) + both server positions from Postgres (not localStorage), 0 console errors.

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
3. ✅ **Analytics / Notes / Logs** screens (task #10) — DONE. `app/(terminal)/analytics` (cards: balance/open-UPNL/realized/fees/win-rate/margin + cumulative-realized chart + net open greeks + closed-trades table), `notes` (per-strategy journal, `addNote`), `logs` (Activity timeline + Ledger). Realized = Σ closed-leg (gross−fees).
4. ✅ **Excel export + Analyse-on-position** — DONE. `lib/exportXlsx.ts` (SheetJS): "Excel" button per strategy → multi-sheet .xlsx (Summary/Legs/MTM/IV/Delta/Theta-Vega/Notes, per-leg columns). `PayoffPanel` takes an optional `legs` prop; "Analyse" button runs the full payoff on a placed position inline. ⚠️ `xlsx@0.18.5` has advisories in its PARSE path (we only `writeFile`).
5. ✅ **DB schema + persistence + server engine** (task #11) — **DONE (all 4 phases) — see "REAL SAVE" above.** Server-authoritative engine + Postgres/Timescale; frontend hydrates from GET /api/state + WS tick. Verified in-browser.
6. **Auth (real login — currently stub user), hosting, Figma import** (task #12).
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
