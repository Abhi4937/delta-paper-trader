# Architecture & Data-Model Reference

_Read this + `HANDOFF.md` first. They replace reading the codebase cold. Last updated: 2026-06-09._

Paper-trading terminal for **Delta Exchange India options** (BTC + ETH). **PAPER-ONLY — read-only Delta key, no order ever placed.** Backend = FastAPI (port 8010, no `--reload`). Frontend = Next.js App Router (port 3000). State is **in-memory** (Zustand on the client; WS cache on the server) — no DB yet.

---

## 1. System map & data flow

```
Delta India WS ──┐
(v2/ticker 5s,   │   MarketDataIngestor (services/market_data.py)
 mark_price ~2s) │   • persistent WS, auto-reconnect
                 └──▶• in-memory tickers cache {symbol: rawTicker, _live_mark}
                         │
   ┌─────────────────────┼───────────────────────────────┐
   ▼                     ▼                                 ▼
/ws/chain (0.3s push)  /api/marks (held syms)      /api/orderbook (L2, REST passthrough)
build_chain()          mark/bid/ask/iv             Delta /v2/l2orderbook
   │                     │
   ▼                     ▼
Frontend api.ts: connectChain(WS) / fetchMarks(1.5s poll) / fetchOrderbook
   │
   ▼
Zustand store (lib/store.ts)  ── singleton; marks(+greeks)/spots/atmIvs maps, legs, positions, ledger
   │                              net+per-leg series sampled 1/sec from open (cap MTM_CAP=12h)
   ▼
Screens:  /chain (chain + builder/orderbook toggle)   /positions (isolated cards + PositionCharts)
```

**Key facts**
- Margin (`/api/margin`) is the **only** path that resolves legs by `product_id` across all expiries (calendars work).
- `_live_mark` preserves the fast `mark_price` value across 5s `v2/ticker` overwrites — this is the realtime-PnL fix.
- Zustand is a module singleton: survives client `<Link>` nav, **resets on hard nav / hot-reload** (positions lost — they're in-memory).
- Backend has **no `--reload`** → restart after every backend edit.

---

## 2. Backend file map (`backend/app/`)
| File | Responsibility |
|---|---|
| `main.py` | App entry, lifespan (owns delta/http/margin/market), `/health`, `/api/marks`, `/api/orderbook`, `/ws`, `/ws/chain` |
| `api/chain.py` | `/api/chain/expiries`, `/api/chain` (REST chain) |
| `api/margin.py` | `/api/margin` (POST) — resolves legs by product_id → MarginService |
| `services/market_data.py` | `MarketDataIngestor` — Delta WS, tickers cache, `chain()`/`expiries()` |
| `services/chain.py` | `normalize_contract`, `build_chain`, `list_expiries`, `default_expiry`; pydantic `Contract/Quote/Greeks/ChainRow/OptionChain` |
| `services/margin.py` | `MarginService` (exact-or-local), `BasketLeg`, `MarginQuote`, `_local` fallback |
| `engines/payoff.py` | Pure: `payoff_at_expiry`, `payoff_profile`, breakevens (TDD) |
| `engines/pnl.py` | Pure: `leg_pnl`, `strategy_pnl` (signed qty × (mark−entry) × cv) |
| `engines/slippage.py` | Pure: `compute_fill`, orderbook walk, bps impact (TDD) |
| `engines/margin.py` | Pure: Black-76 `bs_price`/`implied_vol`, SPAN-like `compute_margin`, stress scenarios |
| `delta/rest.py` | `DeltaRestClient` — HMAC-signed httpx, read-only |
| `config.py` | `Settings` (env), `get_settings()` |

---

## 3. API contracts

### `GET /health` → `{status, version, env}`

### `GET /api/chain/expiries?underlying=BTC`
→ `{underlying: "BTC", expiries: ["2026-06-19", ...]}`  (ISO dates)

### `GET /api/chain?underlying=BTC&expiry=2026-06-19`  (expiry optional → nearest)
→ `OptionChain` (see §4). Live REST snapshot.

### `GET /api/marks?symbols=C-BTC-63000-110626,P-BTC-...`  (comma-sep)
→ `{ "<symbol>": { mark, bid, ask, iv, delta, gamma, theta, vega } }`  — from WS cache; powers held-position MTM + position-analytics greeks. Greeks are Delta's per-option values (read-only); the client aggregates them. Missing symbols omitted.

### `GET /api/atm-iv?underlying=BTC&expiries=2026-06-19,2026-06-26`  (comma-sep ISO)
→ `{ "<expiry>": atm_iv | null }`  — ATM mark-IV per expiry, computed from the WS cache. **ATM IV = average of the ATM call & put `mark_iv`** (fall back to whichever side exists). Delta does not publish an ATM-IV formula (proprietary IV model) — the call/put average is our chosen convention; validate vs Delta's displayed value. Powers the IV analytics panel for held strategies regardless of the chain currently viewed. Read-only.

### `GET /api/orderbook?symbol=<sym>`
→ `{ symbol, buy: [{price,size}...], sell: [...] }`  — Delta `/v2/l2orderbook` passthrough. **0-DTE has no book (404 normal).**

### `POST /api/payoff`
Request: `{ legs:[{option_type,side,qty,strike,entry,iv,contract_value}], spot, lo, hi, points, t_years, iv_shift }`
→ `{ spots[], expiry[], projected[], greeks{delta,gamma,theta,vega}, breakevens[], max_profit, max_loss }`. At-expiry + projected (Black-Scholes r=0 at `t_years`/IV+shift) P&L curves + net greeks at `spot`. Powers the builder's Analyse Payoff (one debounced call per slider settle). Pure: `engines/payoff.py` `projected_pnl`/`net_greeks`, `engines/blackscholes.py`.

### `POST /api/margin`
Request: `{ underlying: "BTC", legs: [{ product_id: int, side: "buy"|"sell", size: int }] }`
→ `MarginQuote`: `{ margin: float, badge: "matched"|"est"|"stale", source: str, ... }`

### `WS /ws/chain`
Client sends once: `{ underlying: "BTC", expiry: "2026-06-19"|null }`
Server pushes every ~0.3s: `{ type: "chain", chain: OptionChain, expiries: string[] }`

### `WS /ws`  — heartbeat only: `{ type: "heartbeat", seq, ts }`

---

## 4. Data model

### Server pydantic (services/chain.py) — wire shape for the chain
`Contract`: symbol, product_id, option_type, strike, mark_price, last_price, oi, oi_value_usd, contract_value, spot_price, quotes{best_bid,best_ask,mark_iv}, greeks{delta,gamma,theta,vega}
`OptionChain`: underlying, expiry, spot, atm_strike, atm_iv, rows[{strike, call, put}]
> Backend snake_case → mapped to camelCase in `frontend/src/lib/api.ts` (`mapContract`/`mapChain`).

### Client state (frontend/src/lib/types.ts) — source of truth for the UI
```ts
Contract  { symbol, productId, type, strike, mark, ltp, bid, ask, iv, oi,
            oiValueUsd, contractValue, greeks }
ChainRow  { strike, call: Contract, put: Contract }
OptionChain { underlying, expiry, dte, spot, atmStrike, atmIv, rows }

Leg  { id, symbol, productId, underlying, type, strike, contractValue, expiry, dte,
       side: "buy"|"sell", qty,
       entry,        // fill premium (crosses spread → includes entry slippage)
       markAtEntry,  // mark @ entry → entry-slippage display
       spotAtEntry,  // spot @ entry → notional fee
       targetPnl, stopPnl,         // per-leg TP/SL on the leg's PnL ($), nullable
       autoExit,                   // auto-exit this leg on its TP/SL
       closeScope: "leg"|"strategy", // on trigger: close this leg, or the whole strategy
       status: "open"|"closed",
       exitPrice, exitAt, exitReason, exitGross, exitFees } // set when the leg closes

Position { id, name, underlying, expiry, legs[], margin, marginBadge:"matched"|"est"|"stale",
           openedAt, status:"open"|"closed", closedAt, closeReason, // close time + why
           targetPnl,                    // combined net target ($), nullable
           stopLossAmount, stopLossPctOfMargin, // combined SL: ₹/$ amount OR % of margin
           autoExit, autoExitSuspended,  // combined auto-exit on / paused (stale marks)
           series: SeriesSample[],    // 1/sec from open, cap MTM_CAP (12h); powers analytics
           notes: {kind:"entry"|"exit", body, at}[] }
SeriesSample { t, pnl, delta, theta, vega, atmIv: { [expiry]: iv }, legs: { [legId]: { pnl, iv, delta } } }
  // net greek = Σ(signed_qty × cv × per-option greek): delta→BTC, theta→$/day, vega→$/vol-pt.
  // per-leg pnl/delta are signed (legs sum to net); per-leg iv is the leg's mark IV.
  // atmIv: one ATM IV per distinct leg-expiry (calendars get an ATM line each).
  // STANDARD aggregation — validate the absolute greek numbers vs a real Delta account.

LedgerEntry { t, type:"deposit"|"margin_reserve"|"margin_release"|"realized"|"fee",
              amount, balanceAfter, ref? }
LogEntry    { t, action, detail, tone? }
```

### Store helpers/money-model (frontend/src/lib/store.ts) — VALIDATED, don't change blindly
- Constants: `START_BALANCE=5000` (USD), `USDINR=85` (fixed), `Currency="USD"|"INR"`.
- Fees (offer-aware): `FEE_SCHEDULES.{standard, optionsCarnival}`, `ACTIVE_FEE=optionsCarnival`, GST 18%, `FEE_DISCOUNT=0`.
- Functions: `legMark`, `legIv`, `spotOf`, `positionPnl`, `entrySlippage`, `entryFee`, `exitFee`, `money`, `moneyBoth`, `fmt`, `startStream()`, `selectLeg/addLeg/toggleLegSide`, `placeStrategy` (keeps basket), `closePosition` (gross−fees).
- Risk/exit: pure `lib/exit.ts` `evaluateExit()` (vitest `exit.test.ts`) decides none/suspended/close-strategy/close-legs from leg PnLs + mark ages + stops. `marks` carry a `ts`; `STALE_MS=10s` suspends auto-exit on stale data. Store: `tickPositions` evaluates each tick; `applyExitActions` runs `closePosition`/`closeLeg`. `closeLeg` realizes one leg + re-fetches `/api/margin` for the rest; closed legs stay in `legs` (status="closed") with an exit record (price/PnL/reason/time) — `openLegs(p)` drives live PnL/greeks/fees. Setters: `setLegRisk` (basket), `setPositionStop` / `setPositionLegRisk` (placed). State persists to localStorage (Zustand `persist`); live data + `series` excluded.
- Position analytics: `marks` map carries greeks; `atmIvs` map (`<u>|<expiry>`→IV); internal `netGreeks`/`sampleLegs`/`buildSample` sample `Position.series` each second (ATM IV per leg-expiry). Rendered by `components/PositionCharts.tsx` (lightweight-charts v5): stacked **MTM/IV/Δ/Θ/Vega**, shared **1s/1m/5m** timeframe + **Line/Candle** on MTM. Net bold; IV shows a bold ATM line **per expiry** + per-leg IVs. Leg colors are grouped by **expiry hue** (CE lighter / PE deeper), consistent for a leg across all panels. Each panel **collapses** (Θ/Vega shorter; collapsing grows the rest); each strategy card collapses (closed ones default collapsed); Close is in the card header. Charts auto-fit start→latest (no manual pan), Y autoscales and always shows the 0-line.
- **MTM = entry-slippage only, no fees.** Close = gross(exit slippage) − entryFee − exitFee. See `money-model-validated.md`.

### Pure engines (backend, TDD anchors)
- `pnl.py`: `leg_pnl(leg, mark)`, `strategy_pnl(legs, marks)` — signed_qty × (mark−entry) × contract_value.
- `payoff.py`: `payoff_profile(legs, ...)` → curve + breakevens + max P/L.
- `slippage.py`: `compute_fill(book, side, qty)` → fill px + bps impact (walks L2, mark±spread fallback flagged).
- `margin.py`: `compute_margin(legs, spot, ...)` Black-76 SPAN-like stress grid; `defined_max_loss` floor.

---

## 5. Planned DB schema (NOT built — task #11)
Postgres + TimescaleDB, multi-user (`user_id` on every owned row + per-query isolation). Relational: `users, instruments, strategies, strategy_legs, paper_orders, paper_order_legs, paper_fills(+JSONB orderbook_snapshot), positions, margin_quotes, virtual_balance_ledger, transaction_log, notes`. Hypertables: `chain_snapshots, atm_iv_series, position_mtm_series, leg_premium_series`. Full detail in the plan: `~/.claude/plans/i-want-to-create-prancy-sloth.md`.

---

## 6. Where things are documented
- `docs/HANDOFF.md` — run commands, done/pending, gotchas.
- `docs/ARCHITECTURE.md` — this file.
- `docs/adr/0001-margin-source.md`, `0002-portfolio-margin-model.md`, `0003-slippage-model.md`.
- `docs/margin-model-research.md` — margin-endpoint capture research.
- Memory `money-model-validated.md` — fee/slippage/MTM validated vs real account @10 lots.
- `CLAUDE.md` (repo root) — golden rules. `software-development-master-checklist.md` — quality gates.
