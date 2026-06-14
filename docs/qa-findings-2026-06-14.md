# QA Findings — Full-System Pass (2026-06-14)

_Comprehensive technical + business-logic review of the Delta India options paper-trading platform. Each finding is written to be **discussed**: **What it is → How it impacts you → What fixing it solves**, plus `file:line` evidence, a trigger, and the fix direction._

## How this was produced
- **Automated suites run:** backend **132/132 pytest pass**, frontend **26/26 vitest pass**, `tsc` + ESLint clean. (Note: an early audit pass wrongly claimed "no tests exist" — that is false; the suites above pass.)
- **Four adversarial audits** (money/risk, API/security, frontend, perf/scaling), with the highest-severity items **re-verified by hand** against the code.
- **Could NOT run** (and why): automated UI E2E against prod — prod has `ALLOW_DEV_NO_AUTH=false` (correct), so a headless browser can't log in; load testing against prod — the 954 MB box is fragile and stalls at ~5–10 positions, so hammering it risks the very outage we're fixing. Both belong on a **local** harness (see "Coverage gaps").

## Verification legend
- ✅ **Verified** — I traced/confirmed it in the code myself this session.
- 🔍 **Traced** — agent-traced with quoted evidence; plausible, not yet independently re-checked.
- ❓ **Suspected** — depends on timing/config/a file not read; needs a test to confirm.

## Severity legend
- **CRITICAL** — wrong money, data loss, or a production outage.
- **HIGH** — wrong number shown, security/DoS exposure, or breaks at realistic scale.
- **MEDIUM** — incorrect edge-case behavior, UX money-traps, latent scaling cost.
- **LOW** — cosmetic, defensive hardening, or far-future scale.

---

# A. Money & accounting correctness (backend)

### A1 · CRITICAL · ✅ Verified — Margin drift is never ledgered, so closing a position invents or destroys money
- **What it is:** At placement, balance is debited by `entry_margin` and a `margin_reserve` ledger row is written (`service.py:239-249`). Over the trade's life the ticker overwrites `pos.margin` with a fresh exact quote every 30 s **with no balance/ledger entry** (`ticker.py:145-155`). At close, the code credits back the *current* drifted margin: `after_release = balance + pos.margin` (`service.py:306`).
- **Impact:** Margin moves as spot/vol move (a strangle's margin can swing materially). If it drifted **up**, the close hands back more than was ever reserved → **your balance grows from nothing**; if it drifted **down**, you're shorted. Your paper P&L and balance stop reconciling against the ledger. This is the single biggest correctness hole. It does **not** contradict `money-model-validated` (that validated fills/fees/MTM, not the reserve accounting).
- **What fixing it solves:** Balance == sum(ledger) at all times; closes return exactly what was reserved; trustworthy account history.
- **Fix direction:** Pick one model and make place/refresh/close consistent — **(a) reserve once at `entry_margin`, release exactly that at close** (keep live margin display-only), or **(b)** post a paired ledger/balance adjustment on every margin delta. (a) is simpler and matches how a broker holds initial margin. Add a conservation test (place → drift margin → close → assert balance == ledger sum).

### A2 · CRITICAL · 🔍 Traced — Partial leg-close mishandles a margin *increase* and a margin-fetch failure
- **What it is:** Closing one leg re-quotes margin for the remainder and computes `released = pos.margin - new_margin` (`service.py:403-467`). Closing the hedge leg of a spread can make the remainder *riskier* → margin rises → `released` is **negative**, written as a `margin_release` row with a negative amount (a reserve mislabeled as a release). On a re-quote failure it falls back to `new_margin = pos.margin` so the freed leg's margin is never returned.
- **Impact:** Same money-conservation family as A1, plus mislabeled ledger rows and a possible uncontrolled balance debit with no negative-balance guard. Mostly hits multi-leg strategies (your Iron Condors).
- **What fixing it solves:** Correct partial-close accounting and a clean ledger; no silent stuck/again-reserved margin.
- **Fix direction:** Folds into A1's chosen model; emit the correct row type for the sign; guard balance ≥ 0 or log when a close would push it negative.

### A3 · HIGH · 🔍 Traced — Stale-feed hard-stop measures loss against entry price, so it can fail to fire
- **What it is:** During the explicit stale-feed hard-stop window the engine enforces a loss-only stop against "last-known marks," but `_mark` returns `leg.entry` for any leg whose quote is missing (`service.py:92-94`), so a dropped leg reverts to 0 P&L.
- **Impact:** Exactly when the safety net is supposed to protect you (bad/partial feed), a real loss can be masked and the protective stop won't trigger. This is a risk-control correctness bug.
- **What fixing it solves:** The hard-stop reflects the true last-known loss; the safety feature actually protects.
- **Fix direction:** Cache the last *good* mark per leg and use it during stale windows; or refuse to hard-stop when any leg's mark is unavailable (don't act on partial data).

### A4 · HIGH · ❓ Suspected — No row locking: a user-close racing the tick (or settlement racing the tick) can double-count
- **What it is:** `close_position`/`close_leg` only check `status == "closed"` read from their own session snapshot; there's no `SELECT … FOR UPDATE`. Settlement runs in a separate session from the main tick (`ticker.py:78-88` vs `89-169`).
- **Impact:** Under READ COMMITTED, an API close and a tick (or settlement + auto-exit) can both see "open," both credit `gross − fees + margin`, and double-apply → realized P&L and margin released **twice**. Rare, but it's real money when it hits.
- **What fixing it solves:** A position can be closed/settled exactly once; no double-count under concurrency.
- **Fix direction:** Lock the position row (`with_for_update()`) and re-check status at the top of every mutation, or use a conditional `UPDATE … WHERE status='open'` and treat 0 rows as "already closed." Add a two-session concurrency test.

### A5 · HIGH · 🔍 Traced — Multi-leg expiry settlement can leave margin stuck on an expired contract
- **What it is:** `settle_expired_legs` closes legs one at a time and each `close_leg` re-quotes margin for the remainder (`service.py:594-602`). After one leg of a same-expiry pair settles, the re-quote runs against a product Delta has just expired → `quote_margin` raises `unknown product_id` → the `except ValueError` keeps the old margin, so it's never released.
- **Impact:** A position can sit half-settled with full margin held on a dead contract until something else overwrites it — capital looks reserved that shouldn't be.
- **What fixing it solves:** Clean, atomic expiry settlement; margin released correctly when the last leg settles.
- **Fix direction:** Settle all legs of a position atomically; never re-quote margin against expired products; release the whole position's margin once the last open leg settles.

### A6 · MEDIUM · ✅ Verified — Fractional lots are truncated to int in the margin call
- **What it is:** `BasketLeg(..., size=int(size))` (`margin_helper.py:43`) while `Leg.qty` is a float. A qty of 0.5 → margin computed for size 0; P&L/fees still use the true qty.
- **Impact:** If any fractional-lot trade is ever placed, its margin is wrong (often zero). Latent today if all quantities are whole lots, but nothing enforces that.
- **What fixing it solves:** Correct margin for any quantity, or a clear rejection of unsupported fractional lots.
- **Fix direction:** Validate/round qty to whole lots at placement, or pass the real (non-truncated) size and confirm Delta accepts it.

### A7 · MEDIUM · 🔍 Traced — A legitimately-zero mark is treated as "no quote" → suppresses a real max-loss
- **What it is:** `_mark`/`build_sample` revert to `entry` when `q.mark == 0` (`service.py:92-94`). A long option that genuinely decays to 0 is a real total loss, not a missing quote.
- **Impact:** Live MTM and the auto-exit P&L show **0 loss** instead of the full premium loss → a max-loss stop may never trigger on a worthless-but-still-open long.
- **What fixing it solves:** Worthless options show their true loss; stops fire correctly.
- **Fix direction:** Distinguish `q is None` (no quote) from `q.mark == 0` (real zero) — preserve `None` through `MarketView` instead of coercing to `0.0`.

### A8 · MEDIUM · 🔍 Traced — `stop_loss_amount = 0` means "exit on any loss"
- **What it is:** `exit_engine.py:40-45` treats `0` as a real floor (`net_pnl <= 0` → close), since it only skips when the value is `None`.
- **Impact:** A user who enters `0` (meaning "no stop" or "breakeven") gets an immediate close on the first cent of loss — surprising behavior.
- **What fixing it solves:** Predictable stop semantics.
- **Fix direction:** Define `0` explicitly (no-stop vs breakeven) and handle it.

### A9 · LOW · 🔍 Traced — Money is never rounded to cents
- **What it is:** Fees/P&L/balance carry full float error; ledger `balance_after` is recomputed per row independently of `account.balance_usd` (`money.py:45-47`, `service.py:307-309`).
- **Impact:** Over many trades, `balance_usd` and the ledger sum can diverge in the last digits — cosmetic now, but it undermines "to-the-cent" claims.
- **What fixing it solves:** Ledger and balance always agree to the cent.
- **Fix direction:** Round money to a fixed precision at every ledger write; keep `balance == last ledger balance_after`.

### A10 · LOW · 🔍 Traced — `spot == 0` at entry permanently zeroes that leg's fee
- **What it is:** `place_strategy` uses `spot_at_entry = mv.spot(...) or 0.0` (`service.py:196`); the fee's notional term is `rate × spot × …`, so spot 0 → fee 0 for that leg forever.
- **Impact:** Rare (the feed-fresh guard makes spot 0 unlikely), but if spot is momentarily missing while option quotes exist, that leg's fee is corrupted for its whole life.
- **What fixing it solves:** Fees always computed against a real spot.
- **Fix direction:** Refuse placement when spot is 0.

> **Note on A-series:** the fill-side fallback (`entry_fill`/`exit_fill`, `money.py:51-58`) was checked and is **acceptable** — a `0` book side falls back to `mark` (then bid/ask), not the opposite side, because the place path passes `q.mark or q.bid or q.ask` as the fallback. Only matters if mark is *also* 0 (covered by A10's spot guard spirit). Not a separate finding.

---

# B. Outage / performance / scaling

### B1 · CRITICAL · 🔍 Traced (confirmable on prod) — `strategy_series` grows forever; this is the real swap-thrash root cause
- **What it is:** The hypertable has **no TimescaleDB compression and no retention policy** (checked all 6 migrations). The ticker writes one row / open position / 10 s with two JSONB blobs.
- **Impact:** ~4–5 MB/day/position uncompressed; your 6 positions ≈ ~1 GB/month on a 954 MB box. The growing working set inflates Postgres's memory/disk footprint → over-commit → swap → the 1 s loop stalls. **Last session's `shared_buffers` cap treated the symptom; this is the cause.** Left alone, the outage returns in weeks.
- **What fixing it solves:** Permanent end to the recurring swap outage; flat disk/RAM growth.
- **Fix direction:** One migration — add a compression policy (compress chunks older than ~1–2 days; JSONB+floats compress ~10–20×) and a retention policy (drop raw chunks past the longest chart window). **Highest-leverage fix in this doc.**

### B2 · CRITICAL · 🔍 Traced — The 30 s margin refresh blocks the event loop with sequential Delta calls per position
- **What it is:** Every 30 s, for each open position, the tick loop awaits **2 blocking Delta REST calls** sequentially inside the single event loop (`ticker.py:145-155` → `quote_margin` → ticker fetch + estimate-margin POST).
- **Impact:** At ~150–400 ms each, your 6 positions ≈ 2–5 s where MTM/auto-exit freezes for **everyone**; one slow Delta response stalls the whole platform. This is likely part of the "feels frozen / can't reach server" you've seen, independent of swap. Gets worse linearly with positions.
- **What fixing it solves:** The 1 s loop stays responsive regardless of position count; health/feed/auto-exit never stall on margin.
- **Fix direction:** Cache the per-underlying ticker map once per tick (it's identical across positions of the same underlying — currently refetched per position); move margin refresh to a bounded background task with a per-call timeout and concurrency cap; skip it when the feed is stale.

### B3 · CRITICAL/HIGH · 🔍 Traced — `spot()` / `atm_iv()` do full-cache scans per position per tick
- **What it is:** `MarketView.spot()` linearly scans all ~2–3k cached symbols, and `atm_iv()` rebuilds the **entire** option chain (normalizes ~1k contracts) — both called inside `build_sample`, which runs per position per second (`marketview.py:50-63`, `service.py:118-155`).
- **Impact:** CPU cost grows as `positions × expiries × symbols`. ~10 positions ≈ tens of thousands of model builds/sec on a single core; saturates the core around ~30–50 positions and starves the WS + market-data ingest.
- **What fixing it solves:** Per-tick CPU drops from `O(positions × symbols)` to `O(symbols)`; headroom to scale positions/users.
- **Fix direction:** Compute spot and ATM-IV **once per underlying per tick** (2 underlyings) and pass them into `build_sample`; maintain an indexed `tickers_by_underlying`.

### B4 · HIGH · 🔍 Traced — The state WebSocket recomputes the whole tick per client
- **What it is:** Each connected client's 1 s WS loop re-queries the DB and re-runs `build_sample` per position (`sim.py:232-254`) — duplicating what the SimTicker already computed, and re-triggering B3's scans per connection/tab.
- **Impact:** Cost scales with `clients × positions`; two browser tabs = double the work. Wasteful at exactly the multi-user scale you're building toward.
- **What fixing it solves:** One computation per tick; WS connections just serialize+send.
- **Fix direction:** Have the SimTicker publish each position's computed sample to a shared in-memory snapshot (or Redis pub/sub per the stack); WS reads the snapshot.

### B5 · HIGH · 🔍 Traced — `get_state` loads ALL ledger + logs unbounded, on every mutation
- **What it is:** No `LIMIT` on the ledger/logs queries (`service.py:860-875`); `get_state` is the response body of every place/close/risk/note action and every page load. No `(user_id, t)` index, so `ORDER BY t DESC` sorts in memory.
- **Impact:** Ledger/logs grow with account age; after months, every click re-serializes thousands of rows into a multi-hundred-KB payload. Steady latency creep.
- **What fixing it solves:** Constant-time state fetches regardless of history size.
- **Fix direction:** Paginate/cap ledger+logs (e.g. last 200) with a "load more"; add a composite `(user_id, t)` index.

### B6 · MEDIUM · 🔍 Traced — Frontend re-`setData`s the full array across 5 panels every second
- **What it is:** Each WS tick changes the data fingerprint, so all 5 chart panels rebuild from the whole series (`PositionCharts.tsx:452-471`); `capPoints` caps output to 1500 but still iterates the full input (up to `MTM_CAP = 48 h`) per panel per leg per second.
- **Impact:** Browser jank on a long-lived expanded position; compounds with multiple expanded cards. (Server unaffected — this is client CPU.)
- **What fixing it solves:** Smooth charts even on multi-hour positions.
- **Fix direction:** Append-only update for the live tail; cap the in-memory tail well below 48 h; memoize `capPoints` output.

### B7 · MEDIUM · 🔍 Traced — Expiry settlement runs inline in the tick loop every 5 min
- **What it is:** `settle_expired_legs` does a second full open-positions scan + an unbounded products fetch + per-leg `close_leg` (each another Delta call), all inside `_tick` (`ticker.py:82`).
- **Impact:** Another blocking network burst on the critical loop; stacks with B2.
- **What fixing it solves:** Settlement can't stall live MTM.
- **Fix direction:** Run settlement in its own background task.

---

# C. API & security

> **Positive findings (audited clean):** per-user data isolation is correct — every sim query filters by `user_id`, including the new `/api/positions/{id}/series` route and the WebSocket; **no cross-user leak.** The AES-GCM secret vault and JWT verification (ES256/JWKS, `aud` enforced, no alg-confusion) are clean. The dev-auth-bypass is double-gated and off in prod.

### C1 · HIGH · 🔍 Traced — Unauthenticated market endpoints are a DoS / Delta-budget lever
- **What it is:** `/api/orderbook`, `/api/marks`, `/api/atm-iv` take no `current_user` (`main.py:106,133,170`); `/api/orderbook` proxies a live Delta REST call per request with your read-only key; `/api/marks` accepts an unbounded symbol list.
- **Impact:** Anyone can loop these unauthenticated → exhaust your shared Delta rate budget for **all** users and load the box. Only the per-IP limiter guards it.
- **What fixing it solves:** Market data is gated to logged-in users; your Delta budget is protected.
- **Fix direction:** Add `current_user` to these reads (the frontend already authenticates); cap symbols per request; tighter bucket for `/api/orderbook`.

### C2 · HIGH · 🔍 Traced — Rate limiter keys on `request.client.host` (the proxy IP behind Caddy)
- **What it is:** `ratelimit.py:29` uses the socket peer, not `X-Forwarded-For`. Behind Caddy, that's often one proxy IP.
- **Impact:** Either all users share one bucket (one user's traffic 429s everyone) or the limit is per-proxy, not per-client — both wrong.
- **What fixing it solves:** Correct per-client/per-user limiting; no false 429s.
- **Fix direction:** Parse a trusted `X-Forwarded-For`, or key the limiter on authenticated `user_id` for authed routes.

### C3 · HIGH · 🔍 Traced — `_account` uses `.scalar_one()` → a missing Account row 500s every mutation
- **What it is:** `_account` (`service.py:72`) hard-fails if a user has no Account row; `get_state` (hence every place/close/risk/note response) and the WS depend on it.
- **Impact:** A half-provisioned user turns the whole app into 500s; also a raw-SQL error in the rollup would surface as a stack-trace 500.
- **What fixing it solves:** Clean error handling; no 500 storms from a provisioning gap.
- **Fix direction:** `scalar_one_or_none()` + explicit 404/clean message; ensure provisioning always creates the Account atomically.

### C4 · MEDIUM · 🔍 Traced — The state WS has no per-tick guard; one bad position drops the feed
- **What it is:** The `while True` WS loop (`sim.py:231-254`) has no inner try/except; an exception serializing one position escapes and kills that user's socket silently.
- **Impact:** A transient market-data glitch disconnects live positions with no log/feedback (compounds F4 below).
- **What fixing it solves:** A resilient live feed that survives a single bad tick.
- **Fix direction:** Wrap the per-tick body in try/except → log and `continue`.

### C5 · MEDIUM · 🔍 Traced — Admin email seed bypasses the allowlist
- **What it is:** A token whose verified email equals `ADMIN_EMAIL` becomes admin and skips the allowlist (`deps.py:51-54`).
- **Impact:** Whoever controls that Supabase email gets admin (cross-user log access) without an allowlist row — fine if `ADMIN_EMAIL` is tightly controlled and email-verified.
- **What fixing it solves:** Defense in depth on admin access.
- **Fix direction:** Require the admin email to also be an allowlist row and assert `email_verified`.

### C6 · MEDIUM · ❓ Suspected — Input bounds on qty / note body / draft basket not confirmed
- **What it is:** `place` only checks `if not req.legs`; qty/note-length/basket-size constraints live in `app/sim/schemas.py` (not yet read).
- **Impact:** Without bounds, negative/huge qty or a giant note/basket blob could be stored or mis-processed.
- **What fixing it solves:** Clean 422s instead of bad data / 500s.
- **Fix direction:** Confirm/add Pydantic constraints (`PositiveInt`, `Field(max_length=…)`, `conlist(max_length=…)`, enums for kind/side/scope). **Triage action: read `schemas.py` to close this out.**

### C7 · MEDIUM · ❓ Suspected — No startup assertion that prod CORS isn't `*`
- **What it is:** Origins come from env; the "never `*`" rule is a comment, not enforced (`main.py:83-89`).
- **Impact:** A misconfig could silently open CORS in prod.
- **What fixing it solves:** Fail-fast on an unsafe origin config.
- **Fix direction:** Assert non-`*`, non-empty origins when `app_env != "dev"`.

---

# D. Frontend & UI

### D1 · HIGH · ✅ Verified — Analytics shows understated open risk (net greeks read the empty lazy series)
- **What it is:** Net open Δ/Θ/Vega sum `p.series[last]` (`analytics/page.tsx:36`), but `/api/state` ships an empty series and it's only filled when you expand a position card on the Positions page.
- **Impact:** Open Analytics without first expanding every card → net exposure is computed from a subset (or zero) → **you see a wrong, understated risk number** on the risk dashboard. Same overhaul-regression family as the bug we just fixed.
- **What fixing it solves:** Correct portfolio greeks always.
- **Fix direction:** Use the server-stamped live `p.delta/p.theta/p.vega` (already on every position) instead of the lazy series tail.

### D2 · HIGH · ✅ Verified — `appendSample` can append an out-of-order point → chart `setData` throws
- **What it is:** `appendSample` only skips samples within 950 ms of the last (`store.ts:122`); it doesn't reject `sample.t <= last.t`. After a lazy-series reload races a WS tick, a slightly-older sample can land out of order; `netMtmCandles`/`dedupBySecond` only collapse *equal* seconds, not descending ones (`chartData.ts:42-69`). lightweight-charts throws on non-ascending time.
- **Impact:** A runtime error and a blank chart panel under a specific race. (Hardening; not constant.)
- **What fixing it solves:** Charts never throw regardless of tick/reload ordering.
- **Fix direction:** In `appendSample`, drop any `sample.t <= last.t`; optionally make the chart preppers defensively skip descending times.

### D3 · HIGH · 🔍 Traced — "Live" dot only tracks the chain socket; a dropped state-WS looks alive
- **What it is:** `connectState` is called without an `onState` status callback (`store.ts:450`), so the header dot reflects only the chain WS + feed (`Shell.tsx:54`).
- **Impact:** If the positions WS drops, live P&L freezes while the dot stays green — a frozen number that looks live, which is dangerous on a trading screen.
- **What fixing it solves:** Honest connection status; you can trust the live numbers or see they're stale.
- **Fix direction:** Surface state-WS status (and a frozen-tick indicator) in the header.

### D4 · HIGH · 🔍 Traced — Close / Close-leg buttons can double-submit
- **What it is:** Neither button disables while the async close is in flight (`positions/page.tsx:171-176, 324-329`); the UI doesn't change until the server responds, so a second click is likely on mobile.
- **Impact:** Two close requests fire; the second's error response is swallowed (no feedback). Mostly harmless given server idempotency, but ties into A4's race.
- **What fixing it solves:** One click = one close, with a spinner and clear feedback.
- **Fix direction:** Disable + spinner while pending; ignore clicks when `status !== "open"`.

### D5 · MEDIUM · 🔍 Traced — Combined-SL input is labelled `₹` but stored/sent as USD
- **What it is:** The RiskPanel SL amount shows a `₹`/`$` label by display currency, but the entered number is sent straight through as USD (`positions/page.tsx:283-292`).
- **Impact:** With INR selected, you type `5000` thinking ₹5,000 but it's stored as **$5,000 ≈ ₹4,25,000** → your stop-loss is 85× off. A real money-correctness trap.
- **What fixing it solves:** The SL you set is the SL that's enforced.
- **Fix direction:** Either always collect SL/TP in USD (drop the ₹ label) or divide the entered INR by 85 before sending.

### D6 · MEDIUM · 🔍 Traced — `place()` isn't awaited → navigates before confirming, no error feedback, basket not cleared
- **What it is:** `StrategyBuilder.tsx:119-130` navigates to /positions without awaiting `placeStrategy`; on failure (returns `null`) nothing is shown, and the basket persists.
- **Impact:** A failed placement looks like it vanished; the retained basket makes an accidental duplicate place one click away.
- **What fixing it solves:** Reliable placement UX; no silent failures or duplicate orders.
- **Fix direction:** `await` the call, navigate only on success, surface failures, clear the basket on success.

### D7 · MEDIUM · 🔍 Traced — `mergePositionsPreservingSeries` keeps a stale series after a partial leg-close
- **What it is:** The fix we just shipped preserves a loaded series whenever the snapshot ships none — including right after a partial close, where the retained series still contains the now-closed leg's contribution until the card is reopened (`serverState.ts`).
- **Impact:** Brief chart discontinuity / slightly-wrong net MTM after closing one leg, until the lazy series refetches. (Feedback on our own fix.)
- **What fixing it solves:** Chart matches the live leg set immediately after a partial close.
- **Fix direction:** Invalidate/refetch the series when a position's leg set changes.

### D8 · MEDIUM · 🔍 Traced — Closed leg can render `exit ` (blank) when `exitPrice` is null
- **What it is:** `exit {l.exitPrice?.toFixed(1)}` (`positions/page.tsx:233`) has no fallback.
- **Impact:** A closed leg missing `exitPrice` shows a broken/blank cell (not a crash).
- **What fixing it solves:** Clean "—" instead of a blank.
- **Fix direction:** `l.exitPrice != null ? l.exitPrice.toFixed(1) : "—"`.

### D9 · MEDIUM · ❓ Suspected — Duplicate WS sockets on foreground-reconnect race
- **What it is:** The `visibilitychange` reconnect can call `connect()` while a retry is also pending and the old socket is still closing (`api.ts:475-481`); `connect()` is async (awaits the token), so two live sockets can result.
- **Impact:** Ticks processed twice/sec → doubled `appendSample` and `tickN`; subtle chart/perf weirdness.
- **What fixing it solves:** Exactly one live socket.
- **Fix direction:** A synchronous "already connecting" guard before the async token fetch; replace `ws` before reconnecting.

### D10 · LOW · 🔍 Traced — Divergence refetch has no backoff
- **What it is:** When `openIds` diverges, `onStateTick` calls `hydrate()`; on failure it re-fires every tick (`store.ts:135-143`).
- **Impact:** A 1-req/sec retry storm on a flaky connection.
- **What fixing it solves:** Graceful degradation when the network is bad.
- **Fix direction:** Add a short cooldown/backoff before re-attempting.

---

# Coverage gaps (asked for, not yet run)
- **Automated UI E2E ("every button works")** — needs a **local** instance with `ALLOW_DEV_NO_AUTH=true` + local Docker DB, then Playwright drives place → positions → charts → close → analytics/notes/logs. Prod can't be driven (no auth bypass — correct).
- **Load / stress testing** — must run **locally**, not on the 954 MB prod box. The perf section already gives quantified thresholds (stalls ~5–10 positions, core-saturates ~30–50, disk-driven OOM in weeks) to validate against.
- **C6** — read `app/sim/schemas.py` to confirm input bounds.

# Suggested priority
1. **B1** (Timescale compression/retention) — ends the recurring outage; one migration.
2. **B2 + B3** (margin refresh + scan off the hot path) — ends the event-loop stall at your scale.
3. **A1/A2** (margin-conservation accounting) — fix the money correctness; add a conservation test.
4. **D1, D5, D3** (wrong risk number, ₹/$ SL trap, frozen-but-green dot) — visible correctness, cheap.
5. **A3/A4/A5** (risk-control + concurrency + settlement) — correctness under stress.
6. **C1/C2/C3** (auth the market endpoints, rate-limit key, 500-proofing) — hardening.
7. Remaining MEDIUM/LOW + local E2E + load harness.
