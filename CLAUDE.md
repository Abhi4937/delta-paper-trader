# Project: Delta Exchange (India) Options Paper-Trading Platform

## What this is
A **hosted, multi-user (few trusted users) paper-trading terminal** for **Delta Exchange India options**. It mirrors the real Delta/Sensibull experience without risking real money:
- **MARKET DATA (read-only):** live option chain, premiums, bid/ask, OI, IV, Greeks, mark/spot — pulled from Delta India REST + WebSocket using a **read-only** key.
- **EXACT MARGIN:** the number Delta's Strategy Builder shows **before placing** (captured read-only endpoint; see `docs/` ADRs). Never an estimate where avoidable.
- **SIMULATION (ours):** orders, fills (slippage model), positions, per-leg & net PnL, real-time MTM, virtual balance, target/SL auto-exit — all simulated in our backend.

**PAPER-TRADE ONLY. No order is ever placed on Delta.** The trade-placing API key is never used — only the read-only key.

This is NOT a throwaway prototype. Optimize for the DORA outcomes and the quality gates in `software-development-master-checklist.md`. The full approved plan lives at `~/.claude/plans/i-want-to-create-prancy-sloth.md`.

## Golden rules
1. **Ask, don't assume.** If a requirement, edge case, data source, contract spec, rounding/precision rule, or risk limit is ambiguous or missing, STOP and ask before writing code. Never invent defaults for anything affecting money, risk, or correctness.
2. **Follow the master checklist.** Treat `software-development-master-checklist.md` as the standard for every phase. Nothing skips the gates because it's "small."
3. **Paper-only, never live.** No code path may place, modify, or cancel a real Delta order. Only the **read-only** key is used. Margin is fetched read-only; nothing is ever placed (not even post-only/cancel).
4. **Use the chosen stack** (below). To deviate, propose it with the trade-off first.
5. **Cite current docs.** Use context7 for version-specific API docs rather than memory. Prefer official sources.
6. **Study before you build.** For any feature mirroring a reference (Delta SB, Sensibull): FIRST produce a spec — API inventory (every endpoint + exact response schema, read from live docs/Playwright, never guessed), metric catalog (formula + source + units + edge cases), UI inventory (every panel + data binding). Review, then implement.
7. **No generic shells.** No mock/placeholder/sample data in committed code; no "simplified subset"; no TODO stubs in shipped features. Every number traces to a real endpoint or a tested calc.
8. **Prove maths + match.** Unit-test each calculation against a known value (textbook Greek, Delta's reported IV, hand-computed payoff). Visual-diff UI vs reference screenshots. No panel ships empty or with fake numbers.
9. **Security.** Keys server-side only, never in repo/client. Per-user data isolation on every query. Validate all inputs; handle rate limits; secrets via env/KMS. Run security-guidance + semgrep on changes.
10. **TDD for engines.** PayoffEngine, PnL, slippage, margin local-model are pure → test-first.

## Stack (defaults — propose before deviating)
- **Backend:** Python 3.12 / **FastAPI** (async) · `httpx` (REST, HMAC-signed) · `websockets` (Delta WS) · `pydantic v2` · `numpy`/`scipy` · `py_vollib_vectorized` (IV/Greeks/Black-76) · SQLAlchemy 2.0 async + Alembic · `asyncpg`. Package mgr: **uv**.
- **Frontend:** **Next.js (App Router) + TypeScript** · **Tailwind** + **shadcn/ui** · TanStack Query (server state) + Zustand (client) + native WebSocket (live) · TanStack Table+Virtual (chain) · **lightweight-charts** (price/MTM) · Recharts/SVG (payoff).
- **Data:** **Postgres + TimescaleDB** (hypertables for 1-min chain/MTM/IV/leg-premium) · **Redis** (pub/sub, sessions, rate-limit).
- **Auth:** Auth.js (NextAuth) + Postgres adapter, or Supabase Auth. Lightweight multi-user.
- **Hosting:** Vercel (frontend) + VPS/Railway/Fly (backend+Redis) + managed/self-hosted Postgres+Timescale. Docker + docker-compose for dev.

## Domain rules
- **Underlyings (v1):** BTC + ETH. **Settlement:** INR. Pin `contract_value`/multiplier/settlement ccy from the `instruments` table; unit-test PnL & margin against Delta SB numbers.
- **Fees:** paper PnL is **net of Delta's real taker/maker fee schedule**.
- **Virtual start balance:** ₹5,00,000 (seeded as one ledger deposit).
- **Multiple strategies are independent:** each has its own legs, margin, positions, MTM series, SL/target, and notes; closing/auto-exiting one never affects another. Each has a separate detail view.
- **Risk/exit:** combined SL on **net capital loss** (₹ or %) AND per-leg SL/target, with a per-leg **"close this leg only / close whole strategy"** toggle. **Auto-exit is suspended on stale marks** (never exit on bad data).
- **Margin source of truth:** captured Strategy-Builder read-only endpoint; local Black-76 stress-test runs in shadow as validator/fallback (badge shows `matched`/`est`/`stale`).
- **No Delta testnet** — it gives unreliable PnL/slippage/margin. Use production read-only data; tests use recorded fixtures/VCR.

## Definition of Done
Compiles · unit + integration tests pass · static analysis + security scan clean · telemetry instrumented · docs/ADR updated · matches acceptance criteria · no mock data in shipped features.

## Repo layout
- `backend/` — FastAPI app, services, db models/migrations, tests.
- `frontend/` — Next.js app.
- `docs/` — ADRs, specs, the margin go/no-go memo.
- `software-development-master-checklist.md` · `claude-code-trading-platform-setup.md` — reference.

## Start here (read before touching code — saves a cold codebase read)
1. `docs/ARCHITECTURE.md` — system map · data flow · API contracts · data model · engines. **Read first.**
2. `docs/HANDOFF.md` — run commands · done/pending · gotchas (backend has no `--reload`; Zustand resets on hard nav).
3. `docs/adr/` — `0001-margin-source` · `0002-portfolio-margin-model` · `0003-slippage-model` (read only when touching margin/slippage).
4. `docs/margin-model-research.md` — margin-endpoint capture research.
- Money model (fees/slippage/MTM) is **validated vs a real Delta account @10 lots** — see memory `money-model-validated.md`; don't change it without re-validating.
- Keep these docs current when the architecture, API, or data model changes (part of Definition of Done).
