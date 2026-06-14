# Session Handoff — Delta Options Paper-Trading Platform

_Last updated: 2026-06-14 — **app is DEPLOYED LIVE at https://trader-abhi.duckdns.org** (Oracle Always-Free VM). Latest session: fixed a recurring production swap-thrash outage, shipped expiry settlement, and designed the position-charts overhaul (spec + plan)._

> **New session: read `docs/ARCHITECTURE.md` first** (system map + API contracts + data model + engines) — it replaces reading the codebase cold. Then this file for run commands & status.
>
> **✅ "Real save" (server-side persistence) is COMPLETE — all 4 phases done. The frontend is now server-authoritative (hydrates from GET /api/state + WS tick). See "REAL SAVE" below for the final architecture.**

## TL;DR
Local paper-trading terminal for **Delta Exchange India options** (BTC + ETH), mirroring Delta/Sensibull. **PAPER-ONLY — read-only key, no order ever placed.** v1 core loop (chain → builder → paper order → positions → live MTM) is **functional end-to-end**, plus Analyse Payoff, Analytics/Notes/Logs screens, Excel export, and Analyse-on-position. Money model (fees/slippage/MTM) is **validated against a real Delta account at 10 lots**.

**State today:** the app is **server-authoritative** (backend `:8010` owns positions/balance/ledger/logs in Postgres+Timescale; frontend hydrates from `GET /api/state` + `WS /api/ws/state`). Plus **real auth, a deploy story, a test/CI safety net, and a responsive UI** — see "LATEST SESSION" below. **A stale dockerized backend may run on :8000 (no sim routes) — ignore it; the frontend targets :8010.**

---

## 🆕 LATEST SESSION (2026-06-14) — read this first

This session: **(1)** diagnosed and permanently fixed a recurring production outage ("can't reach server" / stale feed), **(2)** shipped 1D expiry settlement and verified the earlier deferred batch is live, **(3)** stood up uptime monitoring (waiting on one user step), and **(4)** ran a full brainstorm → spec → plan for the position-charts overhaul. Details below; older sessions follow.

### 🔴 PROBLEM 1 (most important) — recurring production outage, ROOT-CAUSED & FIXED

**Symptom:** Repeatedly over the day, the live app showed "can't reach server" / "Delta market feed is stale" on phone and laptop; positions wouldn't load. Restarting the backend fixed it for a few minutes, then it came back. The user (rightly) pushed: *why does this keep happening and why must we always restart?*

**What it was NOT (false leads ruled out, so the next session doesn't chase them):**
- **Not request volume** — logs showed only ~26 `/api/feed` calls in 3 min (~9/min). No flood.
- **Not OOM-kill** — all containers stayed `Up`; backend sat at ~340 MB.
- **Not the Delta network** — outbound REST to `api.india.delta.exchange` tested at ~200 ms; the `httpx.ConnectTimeout` seen once in logs was a transient blip.
- **Not multiple workers** — single uvicorn worker confirmed (the second PID is just the `uv run` wrapper). Backend listens on **:8010 inside the container** (so `curl localhost:8000` refusing instantly is a red herring — wrong port, not published to host).

**Actual root cause — memory pressure → swap thrash:** The tell was **load average ~2.9 while CPU was only ~34%, with swap in use** = the box was waiting on disk, not computing. TimescaleDB's auto-tuner had sized Postgres `shared_buffers` at **305 MB** (as if Postgres owned the whole machine). On the **954 MB** VM that also runs the backend (~340 MB) + frontend + Caddy + OS, that over-commit forced the kernel into **swap**. The backend's once-per-second async tick loop then **stalled for seconds** paging memory back from disk — and a stalled event loop can't answer `/health` (→ "can't reach server") *and* misses the 20 s Delta WebSocket keepalive ping (→ feed drops → "stale"; 6 WS drops in 10 min in the logs). Restarting only emptied swap; it refilled in minutes. That's why restart was a band-aid.

**The fix (committed + deployed, PR #8):** Cap Postgres memory for a *shared* 1 GB box via `postgres -c` flags in `docker-compose.prod.yml` (these override the auto-tuner): `shared_buffers=96MB`, `maintenance_work_mem=32MB`, `effective_cache_size=256MB`, `work_mem=4MB`, `max_connections=30`. Also added a `mem_limit: 512m` ceiling on the backend (a real leak now OOM-kills + restarts just the backend instead of swapping the whole box). Applied live first via `ALTER SYSTEM` (persists in `postgresql.auto.conf`), then baked into the compose file so a fresh volume gets it too.

**Verified resolved:**

| | During the hang | After the cap |
|---|---|---|
| `/health` latency | 2–8 s, frequent timeouts | **0.07–1.3 s, no timeouts** |
| Delta WS drops | 6 / 10 min | **0 / 3 min** |
| Feed | flapping stale | **fresh** |
| Swap used | 624 MB | **~190–290 MB, draining** |
| RAM available | 178 MB | **418 MB** |
| Postgres `shared_buffers` | 305 MB | **96 MB** |

**Gotcha for next session:** psql prints `setting||unit` with no separator — `shared_buffers` shows as `122888kB`, which is `12288` (8 KB blocks) + `8kB` = **96 MB**, *not* 122,888 KB. Don't misread it.

### ✅ PROBLEM 2 — 1D expiry settlement shipped (PR #7, merged + deployed + verified)
Expired legs used to freeze at entry. Now the tick loop settles open legs past 12:00 UTC at Delta's **published settlement price** (read-only `/v2/products?states=expired`), **fee-free** (owner decision; flagged for later fee validation), every 5 min + on boot. Money path validated 3 ways (unit + live Delta fetch + end-to-end DB balance to the cent). The earlier deferred batch (1B delta lot-independence, 1C time-aware chart cap, 1E Timescale 1-min rollup for old history, 1F lighter 300 s reseed, 3A local Black-Scholes greeks fallback, 3B stale-aware hybrid hard-stop) is all **live** on prod.

### 🟡 PROBLEM 3 — uptime monitoring (VM side DONE, needs one user step)
Set up a dead-man's-switch ping to healthchecks.io so the box going stale pages the user instead of being found by opening the app:
- On the VM: `/usr/local/bin/hc-ping.sh` (curls `/health`, pings only if `ok`), cron `*/2 * * * *`, secret URL in `/etc/hc-ping.url` (mode 600, currently empty → safely no-ops). Script source also in repo at `deploy/hc-ping.sh`.
- **USER TODO:** create a check at healthchecks.io (Period 2 min, Grace 5 min, add a notification), then paste its `https://hc-ping.com/<uuid>` ping URL — it gets written to `/etc/hc-ping.url` and the first cron run (≤2 min) activates the alert.

### 📐 PROBLEM 4 — position-charts overhaul (DESIGNED — spec + plan ready, NOT yet built)
Opening the positions view is slow: `GET /api/state` eagerly builds full per-second history for **all** positions (~50 MB, ~20 s on the 1-vCPU box). Ran a full brainstorm and converged on a design (see below); spec and implementation plan are written and committed on branch **`design/position-live-history-charts`**.

- **Spec:** `docs/superpowers/specs/2026-06-14-position-live-history-charts-design.md`
- **Plan:** `docs/superpowers/plans/2026-06-14-position-live-history-charts.md` (14 TDD tasks)

**Design in one paragraph:** Split the light table snapshot (`GET /api/state`, **no series**) from a new lazy `GET /api/positions/{id}/series` loaded on click. A position's history is served at **one uniform resolution chosen by its age** (≤15m→1s, 15m–12h→10s, 12–24h→1m, >24h→5m) — *not* a mixed-resolution ladder — plus an **always-1s real-time tail** for the last ~15 min (from the in-memory ring + WebSocket). The 10 s DB write now stores **true net-MTM OHLC** (open/high/low/close from the 1 s ticks), and 1m/5m rollups aggregate that OHLC, so the **candle wicks show the real peak the position hit at every zoom** (the prior `last()`-per-bucket rollup silently dropped spikes). Per-leg detail spans the whole life. The manual 1s/1m/5m resolution buttons are **removed** (resolution is automatic). Deferred: zoom-to-load for drilling old regions, and Greek/per-leg OHLC.

**Why these specific design calls (the discussion that produced them, so they aren't re-litigated):**
- *Lazy not eager* — the table only needs the live snapshot; full history per position on click drops the payload from ~50 MB to ~1–4 MB for one position.
- *Uniform-by-age, not a ladder* — the user explicitly did not want mixed resolution within one chart; uniform is simpler, lighter, and the buttons grey naturally.
- *True OHLC at write time* — the user correctly spotted that 10 s *snapshots* don't roll up to true OHLC (a spike between marks is never recorded); storing OHLC at the 10 s write captures it to 1 s precision, permanently. (This reversed an earlier "YAGNI" call.)
- *Candles, no envelope* — the existing candle toggle's wick *is* the high/low, so no extra min/max overlay is needed.

**Next step:** execute the plan (subagent-driven recommended) — it was paused at the execution-choice prompt.

---

## LATEST SESSION (2026-06-12)

### ✅ DEPLOYED LIVE & VERIFIED — `https://trader-abhi.duckdns.org`
The app is **hosted online (free) on an Oracle Cloud Always-Free VM** and verified live this session:
- Homepage `307→/login` over **valid HTTPS** (Caddy auto-cert, Let's Encrypt).
- `/api/feed` → `{"connected":true,"fresh":true}` (backend on the live Delta feed).
- `/health` → `{"status":"ok","env":"production"}`. WS handshake `/ws/chain` → **101**.
- All 4 containers `Up` (db/backend/frontend/caddy); frontend on the freshly-built image.

**VM access & topology**
- SSH: `ssh -i C:\Users\Abhis\.ssh\oracle_paper ubuntu@80.225.219.86` (key `oracle_paper`, `IdentitiesOnly=yes`).
- App dir on VM: `~/delta-paper-trader`. Stack: `docker compose -f docker-compose.prod.yml` → **db (TimescaleDB) + backend(:8010) + frontend(:3000) + Caddy(:80/:443)**, all `restart: unless-stopped`. Caddy routes `/api/* /ws /ws/* /health` → backend, else → frontend.
- Shape: **VM.Standard.E2.1.Micro** (1 GB AMD/x86, always-available free tier) + **2 GB swap** (needed for the Next build — build runs ~6 min, "Compiled successfully in 5.3min" then TS-check; zero downtime, old container serves during build).
- DNS: **DuckDNS** `trader-abhi.duckdns.org` → VM public IP `80.225.219.86`.
- Prod hardening confirmed: `APP_ENV=production`, `ALLOW_DEV_NO_AUTH=false` (no auth bypass in prod — UI behaviours must be checked **logged-in**), `CORS_ORIGINS=https://trader-abhi.duckdns.org`. Oracle security list opens only 80/443/22.

**How to redeploy code to the VM** (private repo → can't `git clone` on VM):
```bash
# from C:\dev\paper_trader  (Git Bash / Bash tool)
git archive --format=tar.gz -o /tmp/pt.tgz main
scp -i ~/.ssh/oracle_paper /tmp/pt.tgz ubuntu@80.225.219.86:/tmp/
ssh -i ~/.ssh/oracle_paper ubuntu@80.225.219.86 \
  'cd ~/delta-paper-trader && tar xzf /tmp/pt.tgz && \
   docker compose -f docker-compose.prod.yml up -d --build > ~/deploy.log 2>&1'
```
`.env.prod` files live on the VM and are **preserved** by the tar-extract (not in the archive). Watch `~/deploy.log`; frontend container recreates only after the ~6-min build finishes (don't trust an early "Up N hours" — that's the old container still serving).
*(Open follow-up: a read-only deploy key / private-clone would make this one command instead of archive+scp.)*

### UI fixes shipped this session (commits `43cf11f` + the WS-reconnect commit; pushed to `origin/main`)
- **WS "down" auto-reconnect** (`lib/api.ts` `connectChain`/`connectState`): exponential backoff (1s→15s) + **foreground-reconnect** on `visibilitychange`. `connectState` re-fetches the access token inside `connect()` so expired tokens refresh on reconnect. **This was the "app goes down after a while" root cause — the server was healthy; the client just never reconnected after a drop (mobile backgrounding / NAT idle / sleep).** Drops are normal and expected; the fix is reconnection, not prevention.
- **SL is NOT affected by WS drops** — confirmed & explained to the user: `ticker.py` runs MTM + auto-exit (SL/target) server-side 24/7 for ALL users independent of any client connection (suspended only on stale marks). The browser WS is display-only.
- **Mobile option chain** (`chain/page.tsx`): all columns kept with h+v scroll centered on ATM; **desktop (lg+) keeps the original hover-reveal B/S**; **mobile taps a strike to expand it in place** (Delta-style) into an inline lot editor pinned to the tapped side of the viewport (`sticky left-0`/`right-0 ml-auto` so it's never off-screen); `text-[16px]` inputs (no iOS zoom); price-flicker removed; mobile scroll-box card framing.
- **Payoff panel** (`PayoffPanel.tsx`): Date/DTE defaults to **current time in IST** with the slider running now→expiry; Y-scale clamp so neither side of zero exceeds 2.5× (fixes tiny green side near expiry); **OI bars rise UP from the 0-line** (were drawn below as huge negatives).
- **Positions** (`positions/page.tsx`): Excel/Close header buttons wrap instead of overflowing the window; **"Loading your positions…" spinner** (persisted `pt:hadOpenPositions` flag + `store.hydrated`) replaces the misleading "No positions yet" flash on cold load.

### Verify-when-logged-in (prod has no bypass — these need your eyes)
Log in at `https://trader-abhi.duckdns.org` and confirm: (1) leave a tab idle / background the phone, come back → it **reconnects** (no permanent "down"); (2) mobile: tap a strike → it **expands in place** with the lot editor on-screen; desktop: hover still shows B/S; (3) payoff Date/DTE shows **current IST** and OI bars sit **above** the 0-line; (4) cold-load Positions shows the **spinner**, not "No positions".

### Still open / next
- **Local dev servers** were left with auth handling as-is for the deploy ("leave it until done deploying") — restore normal local auth flow now that deploy is done, if desired.
- Quiet the **0-DTE orderbook 404 console spam** (settled contracts).
- Read-only **deploy key** for one-command VM updates (see above).
- Prod **monitoring/support tooling** — user deferred ("discuss later").
- Everything under the 2026-06-11 section below still applies (chart in-browser verification, Playwright E2E, lint debt, sub-projects B/C/D).

---

## LATEST SESSION (2026-06-11)

### ⚠️ Many commits are LOCAL & UNPUSHED — push them at session start
`git log origin/main..HEAD` shows the stack. Latest local commit ≈ the chart re-hydrate/Net-Legs fix. **Run `git push origin main` early.**

### Done this session
- **Auth + vault (sub-project A) — DONE.** Supabase identity (Google OAuth, asymmetric **ES256 verified via JWKS** — `app/auth/jwt.py`), invite **allowlist** (`allowed_emails`), per-user **AES-GCM secret vault** (`app/auth/vault.py`, `user_secrets`), admin role. `ensure_stub_user` → `current_user`/`require_admin`/`require_mfa` across `api/sim.py`. **2FA is OPTIONAL for paper; FORCED at every login once a user holds live trade keys** (`/api/me` → `hasLiveKeys`; `AuthGate` enforces aal2). Frontend: `/login`, `/enroll-2fa`, `UserMenu`, gated `settings/keys` (set up 2FA → add keys; "Remove 2FA" once keys cleared), `/admin`, `/live` (onboarding → "coming soon"). Cross-device **draft basket** in `accounts.draft_basket`. Dev bypass = `is_dev && allow_dev_no_auth` (default OFF).
- **Margin self-calibration — DONE.** `margin_calibration` table; `MarginService` EWMA-learns `factor ≈ exact/local` per underlying, applies `local*factor` on fallback when the token is down. Builder shows **exact AND local estimate** + `calibration_factor`. (Slippage can't be calibrated in paper — no real fills.)
- **Per-user tick loop — DONE (was a real bug).** `ticker.py` now ticks **ALL users'** open positions (was stub-only → real users got no MTM history / auto-exit). Durable series write is now **10s** (was 60s); in-memory 1s ring cap **24h**; closed positions' rings are evicted.
- **Deploy artifacts — DONE.** `Dockerfile`s (backend single-worker), `docker-compose.prod.yml` (Timescale + backend + frontend + **Caddy auto-HTTPS**), `Caddyfile`, `.env.prod.example`s, **`docs/DEPLOY.md`** (Oracle Cloud Always-Free + free **DuckDNS** hostname runbook). Redis dropped (unused).
- **Test safety net + CI — DONE.** Backend **84 pytest** (+ `test_exit_engine`, one-sided-book slippage, `test_build_sample` contract, **placement now rejects on stale feed**). Frontend **17 vitest** (chart data-prep extracted to `lib/chartData.ts`). **`.github/workflows/ci.yml`** gates pytest+tsc+vitest+build; ruff/mypy/eslint run **non-blocking** (pre-existing repo debt — see `docs/pre-commit-checklist.md`).
- **Responsive UI (monitoring-first) — DONE.** Shell: left rail ↔ **bottom tab bar** at `<1024px` (pure Tailwind — *custom CSS in globals.css did NOT apply under Tailwind v4*, key gotcha). Positions/Analytics/Chain reflow; dense tables scroll-x. `AuthGate` now **times out to an error screen** instead of spinning forever.
- **Chart improvements — DONE.** "Paper balance"→**"Wallet balance"**; **Net/Legs toggle**; **per-leg theta+vega** (LegSample gained them; `build_sample` emits them); hover/legend shows **expiry** (`61600 PE · 13Jun`); **fixed** per-tick fitContent flicker (fit only on create/tf), the lightweight-ring leg/IV regression (reverted to full samples), Net-toggle blank (constant line count), and the **"from entry" trim** via **90s re-hydrate** (`store.ts` `rehydrateTimer`; client `MTM_CAP`=12h).

### Open / next (in priority order)
1. **Chart "from entry" + per-second hang — CODE DONE, IN-BROWSER VERIFICATION PENDING.**
   - **Server bound** (`service.py` `get_state` → new pure `bounded_series`): returns **full-life 10s DB history + a bounded 5h 1s tail** (`TAIL_1S = 5*60*60`), not the whole 24h ring. Covered by `tests/test_bounded_series.py` (3 tests). ⚠️ **Backend has no `--reload` — RESTART uvicorn to deploy this; if not restarted the chart still trims (old code keeps running).**
   - **Client cap** (`store.ts` `MTM_CAP` → 7-day runaway-guard): was a 12h *element-count* slice that re-trimmed entry-era 10s rows on the first WS tick after each hydrate. Had to ship with the server bound.
   - **Per-second hang fix** (`PositionCharts.tsx`): the page re-renders every second (`positions/page.tsx:25` subscribes `tickN`); the chart's `[net,legs]` effect was doing a full `setData`+`fitContent` on all 5 panels **every render** and on every toggle. Replaced with a unified effect: a plain live tick (exactly one appended sample) does an O(1) `series.update()` with NO refit; only timeframe / candle / Net-Legs / re-create / hydrate-replace does a full `setData`+fit.
   - **NOT yet verified in-browser** (auth-gated, dev-bypass off → can't drive headless). Verify: restart backend, open a position open >12h, confirm chart starts at entry on 1s AND 1m, and that Net/Legs + timeframe toggles are snappy with no per-second flicker.
   - _Future limitation:_ 10s history is full-life (~8,640 pts/day); a position open many weeks grows the payload — add a coarser 1-min tier beyond ~3 days then.
2. **Playwright browser E2E (Task 18)** — data-contract is covered (`test_build_sample` + `chartData.test`); the browser place→render→close layer is NOT built (needs a frontend `NEXT_PUBLIC_E2E` auth bypass + dev-bypass backend; flaky vs live data so it's not in the main CI gate).
3. **Lint/type debt cleanup** to flip CI lint gates to blocking (~21 ruff, 9 mypy, 7 eslint — listed in `docs/pre-commit-checklist.md`).
4. **Sub-projects B/C/D** (live monitoring read-only / live auto-close / journaling) — `/live` is scaffolded. User approved **full auto-close on Delta** (ring-fenced, close-only) for C — see memory `live-trading-expansion.md`.
5. **Auth/security ADR + ARCHITECTURE.md refresh** (old Task 13).

### Session gotchas
- **Backend MUST be single-process** (the tick loop + in-memory ring are per-process — no multiple uvicorn workers without moving the ticker out + ring to Redis).
- **Don't add custom CSS classes to globals.css for layout** — they didn't apply under Tailwind v4 this session; use Tailwind utilities.
- For LAN/phone testing: bind both servers `--host 0.0.0.0` / `next dev -H 0.0.0.0`, point `NEXT_PUBLIC_API_BASE` at the LAN IP, add the LAN origin to `CORS_ORIGINS`, and open a **Windows Firewall** inbound rule for 3000+8010 (home Wi-Fi is often "Public" → blocked by default). All of that was **reverted** at session end (back to localhost).

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
