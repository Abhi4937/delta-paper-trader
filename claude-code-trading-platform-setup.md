# Claude Code Setup for a Production Trading Platform
### Plugins · MCP servers · Skills · Stack · a ready-to-paste CLAUDE.md

**Goal:** make Claude Code a competent senior engineer for *this* project — backend, frontend, DB, design, and quant calculations — without re-explaining things every session, and have it **ask instead of assume** whenever something is ambiguous.

## What this platform is

A **proprietary, single-user algorithmic trading platform** ("trading cockpit") — not an exchange, not a multi-user product. It sits on top of the exchanges and does four jobs:

1. **Aggregates data for monitoring** — live feeds from **Deribit** (reference market for crypto options: chains, IV, greeks, DVOL) and other exchanges, plus **Delta Exchange India** (the execution venue). Historical data is already owned → no data vendor needed.
2. **Computes & displays trading analytics** — the indicators, ratios, and option/future metrics used to *spot* setups (greeks, IV rank, put/call ratio, max pain, skew, OI, funding, basis, RSI/MACD…).
3. **Backtests & automates strategies** — define a strategy, validate it on owned historical data, run live to generate signals.
4. **Executes & manages trades on Delta** — place/modify/cancel orders, track fills, manage positions (entry, stop, exit, sizing) on **Delta only**.

**Driving architectural fact:** **Deribit + other exchanges = read-only data/monitoring; Delta = data + execution.** Deribit *public* market data needs no account.

### Modules (Phase-2 architecture)
| Module | Job | Talks to |
|---|---|---|
| Data ingestion | WebSocket clients → one normalized schema | Deribit (public), Delta, others (via CCXT) |
| Time-series store | owned historical data + live ticks | TimescaleDB |
| Analytics / metrics engine | indicators + option/future metrics (the core) | reads the store |
| Strategy / signal engine | rules → buy/sell signals | reads analytics |
| Backtesting engine | replay owned historical data through a strategy | reads the store |
| Execution & order management | place/modify/cancel, track fills & positions | **Delta only** |
| Risk manager | position/loss limits, kill-switch | gates execution |
| Dashboard | real-time monitoring + manual trade trigger | reads everything |

> Convention (borrowed from your master checklist): **Best default** = what a competent team reaches for absent special constraints. Forks are flagged `→ alt`.
>
> **Not financial advice.** Execution can place *real orders* on Delta. Treat live execution as confirm-first: validate on Deribit testnet / Delta paper-sized funds first, keep live API keys out of the repo, require explicit human confirmation, and ship a kill-switch. See "Money & safety" at the end.

---

## 0 — How the four layers fit together

| Layer | What it does | Where it lives |
|---|---|---|
| **CLAUDE.md** | Standing project knowledge + rules (incl. "ask, don't assume"). Your *master checklist* goes here. | `./CLAUDE.md` (repo root) + `~/.claude/CLAUDE.md` (global) |
| **Plugins** | One-click bundles of skills + MCPs + hooks + subagents | `/plugin` inside Claude Code |
| **MCP servers** | Live tool connections (data, brokers, DB, GitHub…) | `claude mcp add …` or via a plugin |
| **Skills** | Reusable procedure docs Claude loads on demand | `.claude/skills/` or via a plugin |

**First three things to do**
1. Install `claude-code-setup` — it scans your repo and recommends tailored hooks/skills/MCPs/subagents.
2. Install `claude-md-management` — keeps CLAUDE.md accurate as the project grows.
3. Paste your master checklist + the starter block from §5 into `CLAUDE.md`.

**Your reference websites:** don't paste them as throwaway prompts. Either (a) let Claude Code read them live via the **Context7** / **Firecrawl** / **Playwright** plugins, or (b) capture the durable rules into a **custom skill** (see §4) so the patterns persist across sessions.

---

## 1 — General software-development plugins (the "paved road")
Install from Anthropic's official directory:
`/plugin install <name>@claude-plugins-official`
("Anthropic Verified" = extra review. Only install plugins you trust.)

### Setup & project memory
- **claude-code-setup** ✓ — analyze codebase, recommend automations
- **claude-md-management** ✓ — audit/maintain CLAUDE.md
- **remember** — continuous memory across sessions (tiered logs)
- **session-report** ✓ — token/usage/subagent report per session

### Language intelligence (install one per language you use)
- **pyright-lsp** ✓ (Python) · **typescript-lsp** ✓ (TS/JS) · **rust-analyzer-lsp** ✓ (Rust — Nautilus core) · **gopls-lsp** ✓ · **csharp-lsp** ✓ (if you use LEAN) · **jdtls-lsp** ✓ (Java)
- **serena** — semantic code analysis, refactoring, navigation via LSP

### Docs lookup — keeps Claude on *current, correct* APIs (directly fixes "use the best options")
- **context7** — live version-specific docs/examples pulled from source repos
- **microsoft-docs** — Azure/.NET/Windows official docs
- **greptile** / **sourcegraph** — natural-language codebase search across repos

### Feature-dev workflow
- **feature-dev** ✓ — explore → design → review agents
- **superpowers** — brainstorming, subagent dev w/ code review, TDD, skill authoring
- **commit-commands** ✓ — git commit/push/PR workflows
- **code-simplifier** ✓ — simplify recently changed code, preserve behavior
- **ralph-loop** ✓ — iterative "keep working until done" loops for big tasks

### Code review & quality gates (Phase 5–6)
- **code-review** ✓ · **pr-review-toolkit** ✓ — review for comments, tests, errors, types, quality
- **coderabbit** · **optibot** · **sonarqube** · **qodo-skills** — third-party review/quality

### Security — DevSecOps, shift-left (your cross-cutting security track)
- **security-guidance** ✓ — inline warnings + fixes for injection/XSS/unsafe patterns as you edit
- **semgrep** — real-time SAST, guides secure code from the start
- **aikido** (SAST + secrets + IaC) · **sonatype-guide** (supply-chain/dep security) · **endor-labs** (supply-chain risk)

### Testing & browser (Phase 6)
- **playwright** — E2E/browser automation (Microsoft)
- **chrome-devtools-mcp** — inspect a live browser, perf traces, network

### Frontend & design (your frontend track)
- **frontend-design** ✓ — production-grade UI that avoids generic "AI look"
- **figma** — read design files/components/tokens → code
- **playground** ✓ — interactive HTML playgrounds w/ live preview
- **vercel** — deployments, builds, logs, domains

### Backend & database (pick to match §3)
- **supabase** (Postgres + auth + storage + realtime) · **prisma** (Postgres ORM/migrations) · **neon** (serverless Postgres) · **mongodb** · **firebase**
- **postman** — full API lifecycle (you already have this connector)

### Infra / deploy / observability (Phases 7–9)
- **terraform** (IaC) · **deploy-on-aws** · **aws-serverless** · **railway**
- **datadog** (logs/metrics/traces/dashboards) · **sentry** (errors/stack traces) · **posthog** (product analytics, flags, A/B)

### Data engineering (your data track — for tick/market-data pipelines)
- **data-engineering** — warehouse exploration, pipeline authoring, Airflow
- **pinecone** — vector DB (only if you add ML/news-embedding features)

### To build your *own* plugins/MCPs/skills for this project
- **mcp-server-dev** ✓ · **plugin-dev** ✓ · **skill-creator** ✓ · **hookify** ✓

### VCS host (pick one)
- **github** (official MCP) · **gitlab**

> Discovery beyond the official directory: community marketplaces (e.g. `wshobson/agents`, `superpowers`) and registries **mcpservers.org**, **pulsemcp.com**, **glama.ai**, **composio.dev**.

---

## 2 — Data + execution layer (crypto derivatives)

For a crypto-derivatives platform, the data/execution layer is built **in your own backend** (it is not an MCP). Three kinds of source:

### Execution + venue data — Delta Exchange
| Source | Role | Covers | Cost |
|---|---|---|---|
| **Delta Exchange API** | **execution** + its own market data | crypto futures, options, perpetuals (India, INR) | Free w/ account; REST + WebSocket |

This is where orders actually go; use paper-sized funds for the paper-trade phase. (Deribit can also execute, but per the plan you trade on Delta — Deribit is data-only.)

### Monitoring data — Deribit + others (read-only)
| Source | Role | Covers | Cost |
|---|---|---|---|
| **Deribit API** | read-only data; the IV/greeks reference market; **DVOL** | BTC/ETH/SOL options, futures, perpetuals | Free; **public data needs no account** |
| **CCXT** | unified wrapper over Deribit, Delta + 100 more | market data **and** order placement, one API | Free, open-source |

**How it fits:** CCXT handles the common path (order book / trades / candles, place & cancel orders) across venues through one interface. Drop to a native SDK only for venue-specific depth (e.g. Deribit's full options-chain / Greeks endpoints; Deribit testnet `test.deribit.com`).

### Derived analytics data — for the monitoring dashboard (build vs buy)
Every dashboard metric is either **computed from raw Deribit/Delta data** (free, more code) or **pulled ready-made** from an aggregator (paid, less code):
| Source | Best for | Access | Cost |
|---|---|---|---|
| **Coinglass** | liquidations + heatmaps, cross-exchange OI / funding / long-short, max pain — *cannot be self-computed* | REST + WebSocket (V4, `open-api-v4.coinglass.com`) | Paid ~$29/$79/$299 mo; **no free tier** |
| **Laevitas** | ready-made options analytics across 15+ venues: IV/RV, skew, term structure, vol surface, flows, PCR | REST + WebSocket + **MCP** + pay-per-request | Free docs + paid |

**The split to use:** buy **liquidations/aggregates from Coinglass** (you can't compute them); **compute options metrics from free Deribit data** (py_vollib/QuantLib) *or* buy from **Laevitas** if you'd rather not maintain the maths; **always compute indicators yourself** (pandas-ta). Start with Deribit (free) + Coinglass (liquidations) + compute the rest; add Laevitas later.

> MCPs (including Laevitas's) are optional here — only *dev convenience* for pulling data in chat while you build. Your platform's live data/exec path always runs in your own backend over the exchange/aggregator APIs.

### Optional — only if you later add US / traditional markets
None of these touch Deribit/Delta. Ignore unless the roadmap grows to US stocks/options/futures.
- **Alpaca** (official MCP) — US stocks, ETFs, options, spot crypto; **no US futures**; free paper account, same keys swap paper↔live.
- **Interactive Brokers** (`ibkr-mcp`, community) — stocks, options, **futures**, options-on-futures, FX, global; needs TWS/IB Gateway; data subscriptions cost extra.
- **Polygon.io / Databento / Alpha Vantage** — traditional-market *data* vendors only; worth paying for solely if you add US markets.

### Supporting MCPs for building the platform (still useful)
- **github** — issues, PRs, CI from inside Claude Code
- your **database MCP** (TimescaleDB via postgres/prisma, supabase, mongodb) — so Claude can inspect schema & run migrations against *dev*
- **sentry** + **datadog** — wire trade-monitoring alerts and error tracking
- **postman** — design & test your own trading API contracts

---

## 3 — The stack Claude Code should actually build with
*(These are libraries/services, not MCPs — but tell Claude these are the defaults so it doesn't pick something random. This is the "calculation and everything" part.)*

### Strategy / execution engine — the core decision
- **Best default: NautilusTrader** — Rust/Python, event-driven, **backtest → paper → live with the same strategy code**, multi-venue, nanosecond resolution, handles equities/options/futures/crypto. Built specifically to close the research-to-production gap. Adapters for IBKR, Binance, Databento, Polygon, etc.
- `→ alt:` **QuantConnect LEAN** (C#/Python, survivorship-bias-free data, hosted or self-host) if you want a batteries-included professional platform.

### Research / fast backtesting (signal discovery)
- **VectorBT** — vectorized (NumPy/Numba), blazing parameter sweeps over large universes
- **Backtesting.py** — quickest path for a single-strategy prototype + report
- **Backtrader** — feature-rich, realistic event-driven sim, native IBKR
- `→` Common pro workflow: **VectorBT for research → NautilusTrader for execution realism**

### Quant / calculation libraries
- **QuantLib** — pricing, Greeks, yield curves, options/futures valuation (the heavyweight)
- **py_vollib** / **py_lets_be_rational** — fast Black-Scholes / implied vol / greeks
- **pandas**, **numpy**, **scipy**, **statsmodels** — data & stats backbone
- **TA-Lib** or **pandas-ta** — technical indicators
- **PyPortfolioOpt** / **riskfolio-lib** — portfolio optimization & risk
- **empyrical** / **quantstats** — performance & risk metrics (Sharpe, drawdown, etc.)

### Analytics & metrics layer (module #3 — the "find the trade" engine)
*The core of what the dashboard shows. Each group tagged with its source per the §2 build-vs-buy split: 🟢 compute free · 🔵 from Deribit · 🟡 buy (Coinglass/Laevitas).*
- **Technical indicators** 🟢 — RSI, MACD, Bollinger, ATR, VWAP, volume profile: **pandas-ta** `→ alt:` **TA-Lib**.
- **Options greeks & IV** 🔵🟢 — Δ/Γ/Θ/V, implied vol: Deribit & Delta return these in option data; compute extras with **py_vollib** / **py_vollib_vectorized** (fast) · **QuantLib** (advanced).
- **Volatility analytics** 🔵🟢🟡 — IV rank/percentile, skew (25Δ/10Δ, risk reversal, butterfly), term structure, vol surface, RV, IV−RV (VRP): compute from the Deribit chain (**pandas/numpy/scipy**) or pull ready-made from **Laevitas**; **Deribit DVOL** direct.
- **Options positioning** 🟢🟡 — OI & volume by strike/expiry, put/call ratio, max pain, net/dealer gamma (GEX), flows/blocks: compute from the chain; **Coinglass** for options max pain / historical OI.
- **Futures/perp metrics** 🟡 — OI (+ aggregated, dominance), funding (current/predicted/OI-weighted + heatmap), **liquidations (+ heatmap/map)**, long/short ratio, basis vs spot: **Coinglass** (aggregates & liquidations can't be self-computed); per-venue funding/OI also direct from the exchange.
- **Strategy/performance ratios** 🟢 — Sharpe, Sortino, win rate, drawdown: **quantstats** / **empyrical**.

### Backend
- **Best default: FastAPI** (Python — same language as the quant stack; async; WebSockets for live ticks/fills)
- `→ alt:` **NestJS** (TypeScript) if the team is JS-first; you'll cross a language boundary to the quant libs
- Async/background: **Celery** or **arq** + a broker (Redis/RabbitMQ) for backtests & slow jobs
- Realtime push to UI: **WebSockets** (FastAPI native) / SSE

### Databases
- **Time-series (ticks/bars):** **TimescaleDB** (Postgres extension — best default, SQL-native) `→ alt:` **ClickHouse** or **QuestDB** for very high-frequency tick volumes
- **Application / orders / users:** **PostgreSQL**
- **State / cache / pub-sub / rate-limit:** **Redis**

### Frontend (dashboard — Laevitas / Coinglass-style)
- **Best default: Next.js + React + TypeScript**; **shadcn/ui** + **Tailwind** (pairs with the `frontend-design` plugin)
- **Charts:** **TradingView Lightweight Charts** (`lightweight-charts`) for price/candles · **Plotly** or **Apache ECharts** for heatmaps & analytics panels · **D3** / **three.js** for liquidation heatmaps & 3D vol surface
- **Server-state:** **TanStack Query**; **client-state:** **Zustand**; **live updates:** WebSocket subscription
- **Cloning a reference UI:** point Claude Code at the reference URL → **Firecrawl** (page → structured markdown) or **Playwright / chrome-devtools-mcp** (render + screenshot + inspect DOM) → hand it the **screenshots** → the **frontend-design** skill matches layout/spacing/components. Figma files → **figma** plugin → tokens/components. *Replicate UX patterns, not logos/branding/exact copy.*
- Apply your frontend track: Core Web Vitals budgets, CSP/CORS/secure cookies, a11y (axe), i18n

### Infra / ops
- **Docker** + **docker-compose** (dev) → **Kubernetes** or managed (Railway/AWS) at scale
- **Terraform** (IaC) · **GitHub Actions** (CI/CD) · **OpenTelemetry** (traces/metrics/logs) → Datadog/Grafana
- Secrets: **Vault** or cloud KMS (never in repo) — wire from day one

---

## ★ Build discipline — the real thing, not a generic shell (read before building)

**Why generic output happens:** an agent that starts coding before studying a reference fills gaps with plausible defaults, builds a sample of panels instead of all of them, and wires mock data. Fix = a **study → spec → review gate before any UI**, plus completeness + verification rules. This is just enforcing the master checklist's Phase 1 → 2 → 4 gating.

### Lever 1 — Study before build (spec is the deliverable, before UI)
For any feature mirroring a reference (Laevitas / Coinglass / Deribit), Claude Code first produces, for your review:
- **API inventory** — every endpoint used, params, exact response schema field-by-field, **read from live docs via Context7 / Firecrawl / web fetch, never guessed**.
- **Metric catalog** — every metric shown, with formula, source (endpoint *or* computed), units, edge cases.
- **UI inventory** — every panel/chart/widget on the reference + its data binding, from screenshots + DOM via **Playwright**.

Only after you approve the spec does it implement. (= Phase 1 requirements + Phase 2 contract-first design.)

### Lever 2 — Completeness is a hard rule
Definition of Done for a dashboard = the **full UI inventory rendering real data**. No mock/placeholder/sample data committed · no "simplified subset" (N panels in reference → N built) · no TODO stubs in shipped features · every metric traces to a real endpoint or a tested calculation.

### Lever 3 — Verification (prove maths + match)
- **Maths:** you can't read Laevitas's private formulas — implement the **standard model** (Black-Scholes via py_vollib/QuantLib) and **unit-test output against a known value** (textbook greek, or Deribit's reported IV for the same option).
- **UI:** visual-diff against reference screenshots (Playwright).
- **Security:** keys server-side only (never repo/client), dashboard auth, input validation, rate-limit handling — security-guidance + semgrep on every change.
- **Data:** no panel ships empty or with fake numbers.

### The bar — spec required per panel before building it
```
PANEL: ATM Implied Volatility (mirrors Laevitas IV panel)
 API : GET /analytics/options/iv_currency/{currency}
       → { atm_iv, iv_by_tenor[{tenor, iv}], ts }   (fields read from live docs)
       auth: X-API-KEY · rate limit: N/min · cache: 5s
 METRIC: "ATM IV" = market's annualized expected vol from option prices.
       source: endpoint above, OR compute via py_vollib from Deribit marks.
       unit: annualized % · edge: missing strike → interpolate by delta.
 UI  : line chart across 1W/1M/3M/6M/1Y + current-value badge + sparkline;
       layout matches reference; live update over WebSocket.
 TEST: computed ATM IV within 1% of Deribit's reported IV, BTC nearest-expiry.
```

### Workflow
`/study <reference or feature>` → Claude produces the spec above for your review → you approve/correct → `/build` implements panel-by-panel, each bound to real data + a passing test. The **feature-dev** plugin (explore → design → review agents) runs this naturally; the spec is its "design" artifact.

---

## 4 — Skills

### Install (official)
- **skill-creator** ✓ — create/improve/measure skills
- **frontend-design** ✓ — (also a skill) design tokens & UI craft
- **huggingface-skills** / **pydantic-ai** — if/when you add ML or LLM-agent features

### Custom skills to create for *this* project (use `skill-creator`)
1. **`trading-domain`** — house rules: position sizing, risk limits, order types Delta supports, how option/future/perp contracts are modeled, fees/slippage, INR settlement quirks.
2. **`exchange-integration`** — how you connect: Deribit public/testnet data, Delta data+execution, the CCXT-vs-native-SDK boundary, rate limits, reconnection/heartbeat rules.
3. **`options-metrics`** — exact definitions/formulas for the metrics you trade on (IV rank vs percentile, your max-pain/PCR/skew calc, which come from the API vs computed) so they're consistent everywhere.
4. **`backtest-protocol`** — what a valid backtest means on *your* historical data: data hygiene, look-ahead/survivorship checks, walk-forward, reported metrics.
5. **`order-safety`** — the checklist Claude must satisfy before generating any live-order code for Delta (testnet/paper-first, human confirmation, kill-switch, idempotency).
6. **`reference-study`** — the procedure above: how to dissect a reference (Laevitas/Coinglass) into an API inventory + metric catalog + UI inventory spec *before* building. This is the anti-generic-shell skill.

---

## 5 — Ready-to-paste `CLAUDE.md` starter

```markdown
# Project: <Trading Platform Name>

## What this is
A proprietary single-user algo-trading cockpit for crypto derivatives:
- MONITORING: pull data for options/futures analytics from Deribit (+ DVOL),
  Coinglass (liquidations/funding/OI), and optionally Laevitas (vol analytics).
- EXECUTION: place & manage trades on DELTA EXCHANGE INDIA only.
- Show indicators + option/future metrics, backtest on owned historical data,
  automate strategies, manage positions.
This is NOT a throwaway prototype. Optimize for the DORA outcomes and the
quality gates in /docs/software-development-master-checklist.md.

## Golden rules
1. **Ask, don't assume.** If a requirement, edge case, data source, broker,
   contract spec, rounding/precision rule, or risk limit is ambiguous or
   missing, STOP and ask me a specific question before writing code. Do not
   invent defaults for anything that affects money, risk, or correctness.
2. **Follow the master checklist.** Treat /docs/software-development-master-checklist.md
   as the standard for every phase. Nothing skips the gates because it's "small."
3. **Paper-first & live-trade safety.** Never wire live-trading credentials.
   Live order placement is human-confirmed only. Always build/test against
   paper or simulation. Include a kill-switch and idempotent order handling.
4. **Use the chosen stack** (see /docs/claude-code-trading-platform-setup.md §3).
   If you want to deviate, propose it and explain the trade-off first.
5. **Cite current docs.** Use Context7 for version-specific API docs rather
   than relying on memory. Prefer official sources.
6. **Study before you build.** For any feature mirroring a reference (Laevitas,
   Coinglass, Deribit): FIRST produce a spec — API inventory (every endpoint +
   exact response schema, read from live docs, never guessed), metric catalog
   (formula + source + units + edge cases), UI inventory (every panel + its data
   binding, from screenshots/DOM). I review it. Only then implement.
7. **No generic shells.** No mock/placeholder/sample data in committed code; no
   "simplified subset" — if the reference shows N panels, build N; no TODO stubs
   in shipped features. Every metric traces to a real endpoint or a tested calc.
8. **Prove maths + match.** Unit-test each calculation against a known value
   (textbook greek, Deribit's reported IV). Visual-diff UI vs reference
   screenshots. No panel ships empty or with fake numbers.
9. **Security (trading app).** API keys server-side only, never in repo/client;
   dashboard auth; validate all inputs; handle rate limits; secrets via env/KMS.
   Run security-guidance + semgrep on every change.

## Stack (defaults — propose before deviating)
- Engine: NautilusTrader   | Research: VectorBT
- Backend: FastAPI (async) | Frontend: Next.js + React + TS
- DB: TimescaleDB (ticks) + Postgres (app) + Redis (state)
- Quant: QuantLib, py_vollib, pandas/numpy/scipy, pandas-ta, quantstats
- Data + execution: Deribit + Delta Exchange via CCXT (testnet/paper first). No separate broker.

## Definition of Done
Compiles · unit + integration tests pass · static analysis + security scan clean ·
telemetry instrumented · docs/ADR updated · matches acceptance criteria.

## Open questions for me to answer (Claude: surface these early)
- Which exchanges and exact contracts (Deribit? Delta? both? which symbols)?
- Spot, perpetuals, dated futures, options — which of these in v1?
- Regulatory scope (esp. India / FIU for Delta)? Affects compliance track.
- Single-tenant or multi-tenant (SaaS)?
```

---

## 6 — Install quickstart

```bash
# --- Plugins (inside Claude Code) ---
/plugin install claude-code-setup@claude-plugins-official
/plugin install claude-md-management@claude-plugins-official
/plugin install pyright-lsp@claude-plugins-official
/plugin install typescript-lsp@claude-plugins-official
/plugin install context7@claude-plugins-official
/plugin install feature-dev@claude-plugins-official
/plugin install code-review@claude-plugins-official
/plugin install security-guidance@claude-plugins-official
/plugin install playwright@claude-plugins-official
/plugin install frontend-design@claude-plugins-official
/plugin install github@claude-plugins-official
/plugin install skill-creator@claude-plugins-official
# …add backend/DB/infra plugins from §1 to match your stack

# --- Data + execution (built into your backend, NOT an MCP) ---
pip install ccxt          # unified API for Deribit, Delta + 100 more exchanges
#   Deribit: create keys on test.deribit.com (testnet/paper) → then live
#   Delta:   create API keys in your Delta account (api.india.delta.exchange)
#   Use CCXT for the common path; each exchange's native SDK for deep options data.

# --- Optional MCPs (dev convenience only; NOT the live data/exec path) ---
#   Only if you later add US markets: alpaca-mcp-server, ibkr-mcp.

# Run the official directory's recommender once your repo exists:
#   it will suggest hooks/subagents tailored to your code.
```

---

## 7 — Money & safety (read once)

- This document and any AI output are **educational, not investment advice**.
- **Paper-first, always.** Validate in simulation/backtest → paper → tiny live size → scale.
- **Keep live keys out of the repo and out of Claude's reach.** Use paper/read-only keys during development; inject live keys only in a separate, audited deploy path.
- **Human-in-the-loop for live orders.** Treat order submission as a confirm-first action with a kill-switch, position/loss limits, and idempotency keys.
- **Backtests lie if you let them.** Guard against look-ahead bias, survivorship bias, overfitting from parameter sweeps, and unrealistic fills/slippage. Bake these checks into your `backtest-protocol` skill.
- **Compliance is a track, not an afterthought** — scope your jurisdictions early (it changes auth, data retention, reporting).
```
