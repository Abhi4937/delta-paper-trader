# Design: Production Monitoring — Netdata + Slack + Backend APM

_Date: 2026-06-12 · Status: approved (pending written-spec review) · Author: pairing session_

## Goal
Give the live Oracle VM deployment (`https://trader-abhi.duckdns.org`) **persistent, always-on
monitoring** of three layers — **server** (host + containers), **API** (endpoints, latency, errors,
the Delta feed-freshness risk), and **frontend/app issues** — with **Slack alerts** when something
breaks and a **real-time dashboard** for performance/log inspection.

Hard constraint: the VM is **1 GB RAM** (≈377 MiB free, 2 GB swap). The solution must not risk
OOM-killing the actual app, especially during the ~6-min Next.js rebuilds that lean on swap.

## Decision (locked with user — do not re-litigate)
- **Tool: Netdata**, self-hosted on the VM as a single container. Chosen over Grafana Cloud +
  agents because it is purpose-built for a single box, auto-detects system/containers, parses the
  Caddy access log natively, keeps all telemetry **on-box**, and is lighter in total moving parts.
  (Grafana Cloud was the runner-up; its only edge was ad-hoc Loki log search + off-box retention.)
- **Alert channel: Slack** via incoming-webhook (Netdata native Slack notifications).
- **APM layer: YES** — instrument the FastAPI backend with a `GET /metrics` Prometheus endpoint
  (app-level signals), scraped by Netdata. This is what makes it AppDynamics-like rather than
  infra-only.
- Everything stays **PAPER-ONLY / read-only** — monitoring is observe-only; it places no orders and
  touches no trade path. Only the read-only key remains in use.

## Non-goals (YAGNI)
- No Grafana/Prometheus/Loki self-hosted stack (too heavy for 1 GB).
- No Netdata Cloud requirement (optional remote view only; default is on-box + SSH tunnel).
- No custom `/status` HTML page — the Netdata dashboard replaces it.
- No new option-trading logic; no changes to the money/exit/margin engines.

---

## Architecture

```
                         VM (Oracle Always-Free, 1 GB)
  ┌──────────────────────────────────────────────────────────────────┐
  │ docker compose -f docker-compose.prod.yml                          │
  │                                                                    │
  │   caddy ──JSON access log──▶ [shared volume: caddy_logs] ──┐       │
  │   backend  GET /metrics (internal, NOT routed by Caddy) ◀──┤       │
  │   frontend                                                 │       │
  │   db (TimescaleDB)                                         │       │
  │                                                            ▼       │
  │   netdata (NEW, mem-capped ~150 MB, restart: unless-stopped)       │
  │     • cgroups/docker collector  → per-container CPU/mem/io/restart │
  │     • system collector          → CPU/mem/disk/swap/load          │
  │     • web_log collector         → Caddy access log: rate, latency, │
  │                                    status-class (2xx/4xx/5xx)      │
  │     • httpcheck collector       → probe /health, /api/feed, /      │
  │     • prometheus (go.d)         → scrape backend:8010/metrics      │
  │     • health engine ───────────────────────────▶ Slack webhook    │
  │     • dashboard on :19999 (bound to localhost; SSH tunnel)        │
  └──────────────────────────────────────────────────────────────────┘
```

### Component 1 — Netdata container (new compose service)
- Image: `netdata/netdata` (stable), `restart: unless-stopped`, **`mem_limit: 200m`** (hard cap so it
  can never starve the app; expected steady-state ~80–120 MB after tuning).
- Mounts (standard Netdata docker recipe, all read-only where possible): `/proc`, `/sys`,
  `/etc/os-release`, `/var/run/docker.sock:ro` (container discovery), the **`caddy_logs` shared
  volume** (read-only), and our committed config dir.
- `cap_add: [SYS_PTRACE]`, `security_opt: [apparmor=unconfined]` per the Netdata docker requirements.
- **Lean tuning** (`netdata.conf`): disable ML/anomaly engine (`[ml] enabled = no`), keep dbengine
  tier-0 small (per-second short window) with coarser tiers for history; this is the main RAM lever.
- Dashboard port **19999 bound to `127.0.0.1` on the VM only** — viewed via SSH tunnel
  (`ssh -L 19999:localhost:19999 …`). Optional later: a Caddy reverse-proxy at `/netdata` behind
  basic-auth (deferred; default = tunnel, no new public surface).

### Component 2 — Backend `/metrics` APM endpoint (code change, TDD)
- New `GET /metrics` in `backend/app/main.py` returning Prometheus text format.
- **HTTP request histogram** (latency + count by method/route/status) via a small ASGI middleware
  using `prometheus_client` (base lib, minimal deps) — or `prometheus-fastapi-instrumentator` if it
  proves cleaner; either pulls only `prometheus_client`.
- **Custom gauges**, fed from existing state (no new data sources):
  - `app_feed_fresh` (0/1) and `app_feed_age_seconds` — from `app.state.market.feed_status()`
    (same source as `/api/feed`, `main.py:103`). This is the **stale-price risk signal**.
  - `app_open_positions` — count of open positions (aggregate across users).
  - `app_ws_clients` — current chain/state WS client count (if cheaply available; else omit).
- **Security:** `/metrics` is **NOT added to the Caddy route map**, so it is unreachable from the
  public internet. Netdata scrapes it over the internal docker network (`backend:8010/metrics`).
- **Tests** (pytest, per Definition of Done): endpoint returns 200 + valid Prometheus content-type;
  `app_feed_fresh` reflects a mocked fresh vs stale feed; histogram increments on a request.

### Component 3 — Caddy access logging (config-only, zero-downtime)
- Add a `log` directive emitting **JSON** to a file on the `caddy_logs` shared volume.
- Netdata `web_log` (go.d) parses it (`log_type: json`, field map for `status`, `duration`,
  `request.uri`, `request.method`) → request rate, response-time percentiles, status-class split.
- `caddy validate` before reload; reload is zero-downtime (`caddy reload` / compose `up -d` recreates
  only caddy).

### Component 4 — Slack alerting (Netdata health)
- `health_alarm_notify.conf`: `SEND_SLACK="YES"`, `SLACK_WEBHOOK_URL` from **`.env.prod`** (server-side
  only, never in repo), default channel recipient.
- Alarm set (`health.d/paper_trader.conf` + tuned built-ins), each fires once and auto-resolves:
  | Alarm | Condition | Severity |
  |---|---|---|
  | Backend down | httpcheck `/health` ≠ 200 for 2m | critical |
  | **Feed stale** | `app_feed_fresh == 0` for 1m (or `/api/feed` body lacks `"fresh":true`) | critical |
  | Container down/restart-loop | a tracked cgroup disappears / restarts repeatedly | critical |
  | 5xx spike | web_log 5xx rate > 5% over 5m | warning |
  | Disk fill | host disk > 85% warn / > 95% crit | warn/crit |
  | Memory pressure | RAM > 90% | warning |
  | Swap pressure | swap used > 75% (rebuild guard) | warning |
  | API latency | web_log p95 > threshold over 5m (tuned after baseline) | warning |

### Component 5 — Dashboards
- Netdata's auto-generated real-time dashboard covers system, per-container, web_log (API latency /
  status), httpcheck (uptime), and the scraped `app_*` APM metrics. No dashboard-building needed; we
  add a couple of custom chart definitions only if the APM metrics need explicit grouping.

---

## Configuration & secrets
- **Committed to repo** (under `deploy/netdata/`): `netdata.conf`, `go.d/web_log.conf`,
  `go.d/httpcheck.conf`, `go.d/prometheus.conf`, `health.d/paper_trader.conf`, and the Slack stanza of
  `health_alarm_notify.conf` (referencing `${SLACK_WEBHOOK_URL}` — **no secret committed**).
- **VM-only `.env.prod`**: `SLACK_WEBHOOK_URL=…` (preserved by the tar-extract deploy flow).
- `docker-compose.prod.yml`: new `netdata` service + `caddy_logs` named volume mounted into caddy
  (write) and netdata (read).

## User-provided prerequisites (free)
1. **Slack incoming webhook URL** for the target channel.
2. *(Optional)* Netdata Cloud claim token — only if remote/cloud dashboard + cloud alerting is wanted;
   default plan skips it (on-box + SSH tunnel).

## Deployment (existing flow)
1. Backend rebuild for `/metrics` (no `--reload`; container recreate via compose build).
2. Caddy config reload for JSON access log.
3. `netdata` service added; `docker compose -f docker-compose.prod.yml up -d --build`.
4. Verify on VM: dashboard reachable via tunnel, web_log parsing Caddy, `/metrics` scraped, and a
   **deliberately triggered test alarm reaches Slack**.
- Ship via the documented `git archive | scp | compose up -d --build` runbook (HANDOFF.md).

## RAM budget & safety
- Steady state: Netdata ~80–120 MB (ML off, lean tiers) under a 200 MB hard cap. Backend `/metrics`
  adds negligible memory. Leaves headroom on the 377 MiB-free box; the cap guarantees the app is never
  starved even under a metrics spike.
- Rebuild-time pressure is the watch-item; the mem_limit + swap absorb it, and Netdata is
  `restart: unless-stopped` so an OOM of *Netdata itself* (not the app) self-recovers.

## Testing & Definition of Done (per CLAUDE.md)
- Backend `/metrics` unit tests pass (pytest); `tsc`/frontend unchanged.
- `caddy validate` clean; Netdata config validated; container reaches healthy.
- Manual VM verification: all four collectors reporting, dashboard populated, **test Slack alert
  delivered**, feed-stale alarm proven by simulating staleness.
- Docs updated: `docs/HANDOFF.md` (run/monitor section), new `docs/MONITORING.md` runbook (access,
  alarms, tuning, troubleshooting), and an ADR if the approach merits one.
- No mock data; no secret in repo; read-only/paper-only invariants untouched.

## Risks / open items
- **web_log JSON parsing**: confirm Netdata `web_log` field mapping against Caddy's exact JSON schema
  during implementation (validate with a real log sample before relying on alarms).
- **httpcheck body match** for `/api/feed` freshness vs. relying on the scraped `app_feed_fresh`
  gauge — prefer the gauge (precise), keep httpcheck as the up/down backup.
- **mem_limit tuning**: start at 200 MB, measure real usage, tighten if safe.
