# Session Handoff — Delta Options Paper-Trading Platform

_Last updated: 2026-06-10_

> **New session: read `docs/ARCHITECTURE.md` first** (system map + API contracts + data model + engines) — it replaces reading the codebase cold. Then this file for run commands & status.
>
> **🚧 ACTIVE WORK: "Real save" (server-side persistence). Phase 1 done; resume at Phase 2 — see the "REAL SAVE" section below before anything else.**

## TL;DR
Local paper-trading terminal for **Delta Exchange India options** (BTC + ETH), mirroring Delta/Sensibull. **PAPER-ONLY — read-only key, no order ever placed.** v1 core loop (chain → builder → paper order → positions → live MTM) is **functional end-to-end**, plus Analyse Payoff, Analytics/Notes/Logs screens, Excel export, and Analyse-on-position. Money model (fees/slippage/MTM) is **validated against a real Delta account at 10 lots**.

**State today:** the app runs **client-side** (Zustand + localStorage). We are mid-migration to a **server-authoritative engine** with durable Postgres+Timescale persistence ("real save"). Phase 1 (ported money engine + DB schema + live hypertable) is **done & committed**; Phases 2–4 (services/tick-loop, API/WS, frontend swap) remain. **Until Phase 4 lands, the frontend still uses localStorage — nothing is broken.**

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

### TODO — Phase 2: services + tick loop (`backend/app/sim/`, build TDD vs live DB)
- **`user.py`** — `ensure_stub_user(session)` seeds a `User` + `Account` (start balance **$5000**; client `START_BALANCE=5000`) if absent; a `get_current_user` dep returns the stub user id. (Add `STUB_USER_EMAIL` to settings.)
- **`marketview.py`** — adapter over `app.state.market` (the `MarketDataIngestor`). Read live quotes per symbol from **`market.tickers[symbol]`**: `mark_price`, `quotes.best_bid`, `quotes.best_ask`, `quotes.mark_iv`, `greeks.{delta,gamma,theta,vega}` (see `main.py` `/api/marks` lines 77–101 for exact extraction). **spot** = `market.chain(underlying, exp).spot`; **atm_iv** = `market.chain(underlying, exp).atm_iv`; expiries = `market.expiries(underlying)`. Mark age for staleness: tickers carry timestamps via the ingestor (see `market.feed_status()`).
- **`service.py`** — async ops `(session, user_id, market, ...)`, porting the EXACT balance math from `frontend/src/lib/store.ts` (place ~L368, closePosition ~L404, closeLeg ~L448):
  - **place**: entry fill = `entry_fill(side, bid, ask, fallback=mark)`; margin from `MarginService` (Delta exact) else local estimate; reserve = balance−margin; persist Position+Legs + ledger `margin_reserve` (−margin) + log `PLACE`; seed one `strategy_series` row.
  - **close**: gross = `net_pnl` at **exit fills** (`exit_fill`: long→bid, short→ask); fees = entry+exit `leg_fee` per leg; net = gross−fees; balance += margin (release) − fees + gross; ledger realized(gross)/fee(−fees)/margin_release(margin); mark legs closed (exit_price/at/reason/gross/fees).
  - **close_leg**: per-leg realize; re-fetch exact margin for remaining legs; release the diff; last leg closes the strategy.
  - **set_position_risk / set_leg_risk / add_note**.
- **`exit_engine.py`** — port `frontend/src/lib/exit.ts` `evaluateExit` (stale→suspend, combined SL, per-leg TP/SL with leg/strategy scope). Already pure; mirror it + tests.
- **`ticker.py`** — background task (start in `main.py` lifespan after `market.start()`): every ~1s, per open position: recompute MTM from marketview, set `auto_exit_suspended` on stale, run `evaluateExit` → call close/close_leg services; **every 60s** append a `strategy_series` row (net pnl/Δ/Θ/Vega + per-leg `{pnl,iv,delta,bid,ask}` + atm_iv); **every ~30s** refresh exact margin. Update `account.balance` cache.

### TODO — Phase 3: state API + WS (`backend/app/api/sim.py`)
- `GET /api/state` → `{account:{balance,currency}, positions:[…+legs], ledger, logs, notes}` for the user. Pydantic schemas mirroring `types.ts` (camelCase out for the FE, or map in FE).
- POST/PATCH: place, close, leg-close, position-risk, leg-risk, add-note.
- `WS /ws/state` → push live computed positions (current MTM/greeks) + balance every ~1s.
- Stub-user dependency on every route (isolation: filter every query by `user_id`).
- Register router + start ticker in `main.py`.

### TODO — Phase 4: frontend swap (`frontend/src/lib/`)
- `api.ts`: `fetchState`, `placeStrategy`, `closePosition`, `closeLeg`, `setRisk`, `addNote` (POST), `connectState` (WS).
- `store.ts`: actions call the API then refresh from server; hydrate from `GET /api/state` on load; live via WS. Keep component-facing selectors. **One-time import** of existing localStorage state → POST to seed (so nothing is lost). This is the big/risky refactor — components mostly read the store unchanged.

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
5. 🚧 **DB schema + persistence + server engine** (task #11) — **IN PROGRESS — see the "REAL SAVE" section above.** Phase 1 done (engine port + schema + hypertable); Phases 2–4 (services/tick-loop, API/WS, frontend swap) remain.
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
