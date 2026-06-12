# Scaling & Commercialization Roadmap — from trusted users to a sellable product

_Date: 2026-06-12 · Status: draft for review · Owner: Abhishek_

**Goal:** take the platform from **~2 trusted users on one 1 GB VM** to a **multi-tenant product you can sell** to many users — without rebuilding what already works, and with a clear view of the one architectural change that actually gates scale.

> This is a **roadmap** (phased direction + decisions), not an implementation plan. Each phase below
> becomes its own brainstorm → spec → plan → build cycle when we get to it. It deliberately ends with
> a "Decisions I need from you" section, because pricing/target-scale/timeline are business calls.

---

## 1. Current reality (grounded in the codebase)

**Already multi-tenant — do NOT rebuild:**
- Every table is **`user_id`-isolated**; queries filter by user. The schema was built isolation-ready from day one.
- Real **auth**: Supabase Google OAuth, ES256 JWT verified via JWKS; **invite allowlist** (`allowed_emails`); admin role; optional 2FA (forced once a user holds live keys).
- Per-user **AES-GCM secret vault** (`user_secrets`) for any user-held keys.
- The **ticker MTMs all users' open positions 24/7** server-side (auto-exit, SL/target), independent of any browser.
- One **shared read-only Delta feed** fans out to all users via a central ingestor — this design already scales (one upstream connection, N consumers).
- **Server-authoritative** state in Postgres + TimescaleDB; frontend hydrates from `GET /api/state` + WS.

**Net:** you can onboard more users *today* by inviting them. The gap to "sellable" is **commercial plumbing** (signup/billing/legal/support) and **scale/reliability** (infra + the one bottleneck below) — not the core domain model.

---

## 2. The one hard bottleneck: the tick engine

This is the single most important thing on this page.

The `SimTicker` runs **in one process** and keeps an **in-memory per-second ring per open position** (`app.state.sim_series`). Documented invariant (HANDOFF): *"Backend MUST be single-process — the tick loop + in-memory ring are per-process; no multiple uvicorn workers without moving the ticker out + ring to Redis."*

Consequences for scale:
- **CPU + RAM grow with the TOTAL number of open positions across ALL users**, all on one core/process. 2 users is nothing; 200 users each running a few strategies is a different machine.
- You **cannot horizontally scale the backend** (run multiple instances behind a load balancer) while the ticker + rings live in-process — you'd double-tick and corrupt series.

**This is the gate.** Everything else (signup, billing, bigger DB) is standard work. Until the tick engine is externalized, you scale only **vertically** (a bigger box). That's fine for the first cohort of paying users and buys time — but Phase 2 is where it gets fixed properly.

**Options for Phase 2 (decide later):**
- **A. Dedicated ticker worker + Redis rings** — pull the tick loop into its own process/container; move the per-second rings to Redis; backend becomes stateless and horizontally scalable. Lowest conceptual change, keeps one logical ticker.
- **B. Sharded tickers by user/hash** — N ticker workers each own a slice of users; scales past one core. More moving parts (coordination, rebalancing).
- **C. Vertical-only for longer** — just keep buying a bigger box. Cheapest now, hard ceiling later. Reasonable up to a point.

---

## 3. Phased roadmap

### Phase 0 — Observability (IN PROGRESS, this week)
*You can't run a paid service blind.* Already underway on `feat/prod-monitoring`:
- Netdata (system/containers/Caddy-latency/httpcheck) + backend `/metrics` APM + **Slack alerts** + **DB deep monitoring** (connections/locks/deadlocks/durations).
- **Next, queued:** Login/user **RCA** (structured auth+error logs → `/admin` lookup-by-user panel) and the **UI feedback widget** — both are also support/commercial-readiness items.
- **Exit criteria:** when a user reports a problem you can find *which* user, *what* failed, and *why*, and you get paged before they notice.

### Phase 1 — Commercial MVP: first paying users (vertical scale)
Get to "a stranger can sign up and pay," on a bigger-but-still-single box.
- **Self-serve signup** — replace the invite allowlist with open signup (keep an allowlist/waitlist toggle for a soft launch). Email verification.
- **Billing** — Stripe: plan tiers, free trial, paywall gating, webhook → entitlement in `accounts`. Decide the pricing model (see §5).
- **Plan limits** — cap strategies/positions/data-retention per tier (also protects the single box).
- **Legal/compliance** — ToS, privacy policy, and prominent **"paper-trading simulation, not financial advice / not affiliated with Delta"** disclaimers. This matters for a trading-adjacent product.
- **Infra bump** — move off the 1 GB Always-Free VM to a right-sized instance + **managed Postgres/Timescale** (so DB isn't competing with the app for 1 GB). Backups.
- **Support surface** — feedback widget (Phase 0) + a public status page.
- **Exit criteria:** a new user signs up, pays, trades paper, and you can support them — all without you touching the VM.

### Phase 2 — Horizontal scale: fix the bottleneck
Triggered when one box can't hold the open-position load (watch the Phase-0 metrics: backend CPU pinned, RAM near cap, tick-loop latency rising).
- **Externalize the tick engine** (Option A/B from §2): dedicated ticker worker + Redis-backed rings.
- **Stateless backend** behind a load balancer → run N instances, rolling deploys, no downtime.
- **DB scale** — Timescale **retention + compression** policies, read replicas if needed, connection pooling (PgBouncer).
- **Exit criteria:** add backend capacity by adding instances; tick load scales independently of API load.

### Phase 3 — Hardening & growth
- Abuse/rate-limit per user (the Delta key is shared — protect it), anomaly alerts, per-tenant quotas.
- SLA/uptime targets, on-call, incident runbooks (build on Phase-0 monitoring).
- Onboarding polish, analytics/funnel, referral, admin tooling maturity.

---

## 4. Infra & cost evolution (rough)
- **Now:** Oracle Always-Free 1 GB VM — ₹0. Fine for trusted users; too small to sell on.
- **Phase 1:** small VPS/managed app host (~2–4 GB) + managed Postgres/Timescale — order of a few thousand ₹/month. Stripe takes a % per transaction.
- **Phase 2+:** scales with users — multiple app instances + Redis + managed DB tier. Driven by **total open positions** (tick load) more than raw user count.

## 5. Decisions I need from you (business — I won't assume these)
1. **Target scale & timeline** — "first 50 paying users in 3 months" vs "just open it to a wider friends group." This sets whether we even need Phase 2 soon.
2. **Pricing model** — flat monthly? freemium (free tier + paid)? one-time? This shapes the billing + limits work.
3. **Signup posture** — fully open, or waitlist/approval at first?
4. **Budget appetite** for infra (decides how long vertical-scale-only is acceptable before Phase 2).
5. **Compliance comfort** — happy with a clear "paper-only simulation, not advice" disclaimer, or do you want a lawyer's eyes before public launch?

## 6. How this sequences with the current threads
- **Phase 0 is the current work.** Finish monitoring (done: system/API/DB) → build **RCA** + **feedback** next (already approved) — both double as Phase-0 support tooling.
- **Then** pick a Phase-1 start once you answer §5. Phase 2 (the ticker rework) is **not** urgent for 2 users — we hold it until the metrics say so, which is exactly why Phase 0 comes first.
