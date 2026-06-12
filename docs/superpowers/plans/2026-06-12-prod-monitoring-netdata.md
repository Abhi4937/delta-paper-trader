# Production Monitoring (Netdata + Slack + Backend APM) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add always-on, on-box monitoring to the live Oracle VM — Netdata agent (system + containers + Caddy access-log latency + endpoint probes), a backend `/metrics` APM layer, and Slack alerts — without risking OOM on the 1 GB box.

**Architecture:** One new `netdata` container (mem-capped) joins the prod compose stack on the internal network. It scrapes the backend's new Prometheus `/metrics` endpoint, reads `/var/run/docker.sock` for per-container stats, mounts host `/proc` `/sys` for system stats, and tails the Caddy JSON access log (new shared volume) for real request latency/status. Netdata's health engine posts alerts to a Slack webhook. The `/metrics` route is deliberately **not** added to the Caddy route map, so it is unreachable publicly.

**Tech Stack:** FastAPI + `prometheus_client` (backend APM), Netdata `stable` (agent), Caddy JSON logging, Docker Compose, Slack incoming webhook.

---

## File map

**Backend APM (TDD):**
- Create `backend/app/metrics.py` — Prometheus registry, request-latency histogram, app gauges, timing middleware, `render_metrics()`.
- Modify `backend/app/main.py` — register middleware + `GET /metrics` route.
- Create `backend/tests/test_metrics.py` — unit tests.
- Modify `backend/pyproject.toml` — add `prometheus-client` dependency.

**Caddy access log:**
- Modify `Caddyfile` — JSON access log to a file.
- Modify `docker-compose.prod.yml` — `caddy_logs` volume mounted into caddy.

**Netdata agent + config:**
- Modify `docker-compose.prod.yml` — `netdata` service + named volumes.
- Create `deploy/netdata/netdata.conf` — lean tuning (ML off).
- Create `deploy/netdata/go.d/prometheus.conf` — scrape backend `/metrics`.
- Create `deploy/netdata/go.d/httpcheck.conf` — probe `/health`, `/api/feed`.
- Create `deploy/netdata/go.d/web_log.conf` — parse Caddy JSON log.
- Create `deploy/netdata/health.d/paper_trader.conf` — custom Slack alarms.
- Create `deploy/netdata/health_alarm_notify.conf` — Slack stanza.
- Modify `.env.prod.example` (root) — document `SLACK_WEBHOOK_URL`.

**Docs:**
- Create `docs/MONITORING.md` — runbook.
- Modify `docs/HANDOFF.md` — point at monitoring.

---

## Task 1: Add the `prometheus-client` dependency

**Files:**
- Modify: `backend/pyproject.toml`

- [ ] **Step 1: Add the dependency**

In `backend/pyproject.toml`, inside the `dependencies = [` list (after the `httpx>=0.28` line), add:

```toml
    "prometheus-client>=0.21",
```

- [ ] **Step 2: Sync the environment**

Run: `cd backend; uv sync`
Expected: resolves and installs `prometheus-client` with no errors.

- [ ] **Step 3: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock
git commit -m "build(backend): add prometheus-client for the /metrics APM layer"
```

---

## Task 2: Backend metrics module (TDD)

**Files:**
- Create: `backend/app/metrics.py`
- Test: `backend/tests/test_metrics.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_metrics.py`:

```python
"""Unit tests for the Prometheus /metrics APM layer."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app import metrics as m
from app.main import app

client = TestClient(app)


class _FakeMarket:
    """Stand-in for MarketDataIngestor: only feed_status() is used by /metrics."""

    def __init__(self, *, fresh: bool, age: float | None) -> None:
        self._fresh, self._age = fresh, age

    def feed_status(self) -> dict[str, object]:
        return {"connected": True, "age_seconds": self._age, "fresh": self._fresh}


def test_metrics_endpoint_exposes_prometheus_text() -> None:
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    assert "http_request_duration_seconds" in resp.text
    assert "app_feed_fresh" in resp.text


def test_feed_fresh_gauge_reflects_market_state() -> None:
    app.state.market = _FakeMarket(fresh=True, age=1.2)
    client.get("/metrics")
    assert m.REGISTRY.get_sample_value("app_feed_fresh") == 1.0

    app.state.market = _FakeMarket(fresh=False, age=30.0)
    client.get("/metrics")
    assert m.REGISTRY.get_sample_value("app_feed_fresh") == 0.0


def test_request_histogram_increments_per_request() -> None:
    labels = {"method": "GET", "route": "/health", "status": "200"}
    before = m.REGISTRY.get_sample_value("http_request_duration_seconds_count", labels) or 0.0
    client.get("/health")
    after = m.REGISTRY.get_sample_value("http_request_duration_seconds_count", labels) or 0.0
    assert after == before + 1.0
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend; uv run pytest tests/test_metrics.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.metrics'` (and the `/metrics` route 404s).

- [ ] **Step 3: Write the metrics module**

Create `backend/app/metrics.py`:

```python
"""Prometheus metrics — the backend APM layer scraped by Netdata.

Exposes an HTTP request-latency histogram (labelled by the matched route template,
NOT the raw path, to bound cardinality) plus app-level gauges (Delta feed freshness
and tracked open positions). Served at GET /metrics, which is intentionally NOT in
the Caddy route map, so it is only reachable on the internal docker network
(Netdata scrapes backend:8010/metrics). Observe-only: no secrets, no trade path.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    Gauge,
    Histogram,
    generate_latest,
)
from starlette.middleware.base import BaseHTTPMiddleware

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from fastapi import FastAPI
    from starlette.requests import Request
    from starlette.responses import Response

REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency by method, matched route template and status code.",
    ["method", "route", "status"],
)
FEED_FRESH = Gauge("app_feed_fresh", "1 if the Delta market-data feed is fresh, else 0.")
FEED_AGE = Gauge("app_feed_age_seconds", "Seconds since the last Delta feed message (-1 if unknown).")
OPEN_POSITIONS = Gauge("app_open_positions", "Currently tracked open positions across all users.")


class PrometheusMiddleware(BaseHTTPMiddleware):
    """Time every request into REQUEST_LATENCY, labelled by the matched route template."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        start = time.perf_counter()
        response = await call_next(request)
        route = request.scope.get("route")
        template = getattr(route, "path", None) or request.url.path
        REQUEST_LATENCY.labels(request.method, template, str(response.status_code)).observe(
            time.perf_counter() - start
        )
        return response


def render_metrics(app: FastAPI) -> tuple[bytes, str]:
    """Refresh app-level gauges from app.state, then render the exposition payload."""
    market = getattr(app.state, "market", None)
    if market is not None:
        status = market.feed_status()
        FEED_FRESH.set(1.0 if status.get("fresh") else 0.0)
        age = status.get("age_seconds")
        FEED_AGE.set(float(age) if age is not None else -1.0)
    series = getattr(app.state, "sim_series", None)
    if series is not None:
        OPEN_POSITIONS.set(float(len(series)))
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST
```

- [ ] **Step 4: Wire the middleware and route into `main.py`**

In `backend/app/main.py`, add the import near the other `app.` imports (after line 33, `from app.ratelimit import RateLimiter`):

```python
from app.metrics import PrometheusMiddleware, render_metrics
```

Add the timing middleware immediately BEFORE the CORS block (so CORS stays outermost). Insert just above the `# CORS is added last` comment (around line 81):

```python
app.add_middleware(PrometheusMiddleware)
```

Add the route handler right after the `/api/feed` handler (after line 103). Note the `Response` import — add `Response` to the existing `from fastapi import ...` line (currently `from fastapi import FastAPI, WebSocket, WebSocketDisconnect`):

```python
@app.get("/metrics")
async def metrics() -> Response:
    """Prometheus exposition for Netdata. NOT routed by Caddy — internal scrape only."""
    payload, content_type = render_metrics(app)
    return Response(content=payload, media_type=content_type)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend; uv run pytest tests/test_metrics.py -v`
Expected: all 3 tests PASS.

- [ ] **Step 6: Run the full suite + type check (no regressions)**

Run: `cd backend; uv run pytest -q; uv run mypy app/metrics.py`
Expected: full suite still green (was 84 tests + 3 new = 87), `mypy app/metrics.py` clean.

- [ ] **Step 7: Commit**

```bash
git add backend/app/metrics.py backend/app/main.py backend/tests/test_metrics.py
git commit -m "feat(backend): Prometheus /metrics APM (feed-fresh + per-route latency), internal-only"
```

---

## Task 3: Caddy JSON access logging

**Files:**
- Modify: `Caddyfile`
- Modify: `docker-compose.prod.yml`

- [ ] **Step 1: Add the log directive to `Caddyfile`**

Replace the contents of `Caddyfile` with (adds a `log` block writing JSON to a rolled file; route map unchanged so `/metrics` stays unrouted/non-public):

```
# Auto-HTTPS reverse proxy. Caddy fetches a Let's Encrypt cert for $DOMAIN automatically
# (ports 80/443 must be open + DNS must point here). WebSockets pass through natively.
{$DOMAIN} {
	# JSON access log → shared volume, tailed by Netdata's web_log collector.
	log {
		output file /var/log/caddy/access.log {
			roll_size 10MiB
			roll_keep 5
		}
		format json
	}
	# backend: API, websockets, health
	@backend path /api/* /ws /ws/* /health
	handle @backend {
		reverse_proxy backend:8010
	}
	# everything else → the Next.js frontend
	handle {
		reverse_proxy frontend:3000
	}
}
```

- [ ] **Step 2: Mount a log volume into the caddy service**

In `docker-compose.prod.yml`, in the `caddy:` service `volumes:` list (currently lines 55-58), add a fourth entry:

```yaml
      - caddy_logs:/var/log/caddy
```

And in the top-level `volumes:` block (currently `pgdata: / caddydata: / caddyconfig:`) add:

```yaml
  caddy_logs:
```

- [ ] **Step 3: Validate the Caddyfile syntax**

Run: `docker run --rm -v "${PWD}/Caddyfile:/etc/caddy/Caddyfile:ro" caddy:2-alpine caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile`
Expected: `Valid configuration` (the `{$DOMAIN}` placeholder warning is fine).
(PowerShell: use `${PWD}`; Git Bash: use `$PWD`.)

- [ ] **Step 4: Commit**

```bash
git add Caddyfile docker-compose.prod.yml
git commit -m "feat(caddy): JSON access log to shared volume for Netdata web_log"
```

---

## Task 4: Netdata config files

**Files:**
- Create: `deploy/netdata/netdata.conf`
- Create: `deploy/netdata/go.d/prometheus.conf`
- Create: `deploy/netdata/go.d/httpcheck.conf`
- Create: `deploy/netdata/go.d/web_log.conf`
- Create: `deploy/netdata/health.d/paper_trader.conf`
- Create: `deploy/netdata/health_alarm_notify.conf`

- [ ] **Step 1: Lean agent tuning** — create `deploy/netdata/netdata.conf`:

```ini
# Lean config for a 1 GB VM: disable the ML/anomaly engine (largest RAM lever)
# and keep the on-disk metrics store small. Everything else uses stock defaults.
[global]
    hostname = paper-trader-vm

[ml]
    enabled = no

[db]
    mode = dbengine
    storage tiers = 2
    dbengine tier 0 retention size = 256MiB
    dbengine tier 1 retention size = 128MiB

[health]
    enabled = yes
```

- [ ] **Step 2: Scrape the backend `/metrics`** — create `deploy/netdata/go.d/prometheus.conf`:

```yaml
# Scrape the FastAPI APM endpoint over the internal docker network.
jobs:
  - name: paper_backend
    url: http://backend:8010/metrics
```

- [ ] **Step 3: Endpoint probes** — create `deploy/netdata/go.d/httpcheck.conf`:

```yaml
# Synthetic up/down probes (work even at zero real traffic). api_feed also asserts
# the feed-freshness flag in the JSON body as a backup to the app_feed_fresh gauge.
jobs:
  - name: health
    url: https://trader-abhi.duckdns.org/health
    status_accepted: [200]
    timeout: 5

  - name: api_feed
    url: https://trader-abhi.duckdns.org/api/feed
    status_accepted: [200]
    response_match: '"fresh":true'
    timeout: 5
```

- [ ] **Step 4: Caddy access-log parsing** — create `deploy/netdata/go.d/web_log.conf`:

```yaml
# Parse Caddy's JSON access log for request rate, latency and status-class split.
# NOTE: confirm the JSON field mapping against a real log sample in Task 6 Step 4 —
# Caddy emits `status` (int), `duration` (seconds, float), `request.method`,
# `request.uri`. Adjust `mapping:` if the validated sample differs.
jobs:
  - name: caddy
    path: /host/caddy/logs/access.log
    log_type: json
    json:
      mapping:
        status: status
        request_time: duration
        method: request.method
        url: request.uri
```

- [ ] **Step 5: Custom Slack alarms** — create `deploy/netdata/health.d/paper_trader.conf`:

```ini
# Feed-staleness is the core trading risk (auto-exit suspends on stale marks).
# app_feed_fresh is scraped from the backend /metrics gauge. The chart/dimension
# id below follows Netdata's prometheus collector naming; VERIFY the exact id on
# the dashboard (Task 6 Step 5) and correct `on:`/`lookup:` if it differs.
 template: paper_feed_stale
       on: prometheus_paper_backend.app_feed_fresh
   lookup: average -1m unaligned
    units: fresh
    every: 30s
     warn: $this < 1
     crit: $this < 1
    delay: up 60s down 60s
     info: Delta market-data feed is STALE — trading risk, auto-exit suspended
       to: sysadmin

# Backend liveness via the httpcheck probe (0 = request failed / non-200).
 template: paper_backend_down
       on: httpcheck_health.request_status
   lookup: average -2m unaligned of success
    every: 30s
     crit: $this < 1
    delay: up 120s down 60s
     info: Backend /health probe is failing
       to: sysadmin

# 5xx error-rate spike from the Caddy access log.
 template: paper_5xx_rate
       on: web_log_caddy.requests_by_status_code_class
   lookup: sum -5m unaligned of 5xx
     calc: $this
    units: requests
    every: 1m
     warn: $this > 0
    delay: up 60s down 300s
     info: 5xx responses observed at the edge in the last 5 minutes
       to: sysadmin
```

- [ ] **Step 6: Slack notification stanza** — create `deploy/netdata/health_alarm_notify.conf`:

```sh
#!/usr/bin/env bash
# Netdata sources this as bash, so ${SLACK_WEBHOOK_URL} expands from the container env.
# The webhook secret itself lives ONLY in the VM .env.prod, never in this file.
SEND_SLACK="YES"
SLACK_WEBHOOK_URL="${SLACK_WEBHOOK_URL}"
DEFAULT_RECIPIENT_SLACK="alerts"
```

- [ ] **Step 7: Commit**

```bash
git add deploy/netdata
git commit -m "feat(monitoring): Netdata config — lean tuning, backend scrape, probes, web_log, Slack alarms"
```

---

## Task 5: Netdata compose service + env example

**Files:**
- Modify: `docker-compose.prod.yml`
- Modify: `.env.prod.example`

- [ ] **Step 1: Add the `netdata` service**

In `docker-compose.prod.yml`, add this service after the `caddy:` service (before the top-level `volumes:` block). It joins the default compose network (so `backend:8010` resolves), is memory-capped, reads host stats + docker.sock + the Caddy log volume, and publishes its dashboard to localhost only:

```yaml
  netdata:
    image: netdata/netdata:stable
    pid: host
    restart: unless-stopped
    mem_limit: 250m
    cap_add:
      - SYS_PTRACE
      - SYS_ADMIN
    security_opt:
      - apparmor:unconfined
    ports:
      - "127.0.0.1:19999:19999"   # dashboard — localhost only, reach via SSH tunnel
    environment:
      SLACK_WEBHOOK_URL: ${SLACK_WEBHOOK_URL:?set SLACK_WEBHOOK_URL in .env}
    volumes:
      - netdatalib:/var/lib/netdata
      - netdatacache:/var/cache/netdata
      - ./deploy/netdata/netdata.conf:/etc/netdata/netdata.conf:ro
      - ./deploy/netdata/go.d:/etc/netdata/go.d:ro
      - ./deploy/netdata/health.d:/etc/netdata/health.d:ro
      - ./deploy/netdata/health_alarm_notify.conf:/etc/netdata/health_alarm_notify.conf:ro
      - /proc:/host/proc:ro
      - /sys:/host/sys:ro
      - /etc/os-release:/host/etc/os-release:ro
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - caddy_logs:/host/caddy/logs:ro
    depends_on:
      - backend
      - caddy
```

- [ ] **Step 2: Add the Netdata volumes**

In the top-level `volumes:` block, add:

```yaml
  netdatalib:
  netdatacache:
```

- [ ] **Step 3: Document the new secret in `.env.prod.example`**

Append to `.env.prod.example` (root):

```sh
# Slack incoming-webhook URL for Netdata alerts (server-side only; never commit the real value).
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/XXX/YYY/ZZZ
```

- [ ] **Step 4: Validate the compose file**

Run: `SLACK_WEBHOOK_URL=x DOMAIN=x POSTGRES_PASSWORD=x SUPABASE_URL=x SUPABASE_ANON_KEY=x docker compose -f docker-compose.prod.yml config -q`
Expected: no output (valid). (Git Bash; on PowerShell set the vars with `$env:` first.)

- [ ] **Step 5: Commit**

```bash
git add docker-compose.prod.yml .env.prod.example
git commit -m "feat(monitoring): add mem-capped Netdata service + localhost dashboard + Slack env"
```

---

## Task 6: Deploy to the VM and verify (manual)

**Pre-req:** the user has created a **Slack incoming webhook** and given you the URL. On the VM, add it to the prod env file Netdata reads (the root `.env` used by `docker-compose.prod.yml`):
`echo 'SLACK_WEBHOOK_URL=https://hooks.slack.com/services/…' >> ~/delta-paper-trader/.env`

- [ ] **Step 1: Ship the branch to the VM** (from `C:\dev\paper_trader`, Git Bash)

```bash
git archive --format=tar.gz -o /tmp/pt.tgz feat/prod-monitoring
scp -i ~/.ssh/oracle_paper /tmp/pt.tgz ubuntu@80.225.219.86:/tmp/
ssh -i ~/.ssh/oracle_paper ubuntu@80.225.219.86 \
  'cd ~/delta-paper-trader && tar xzf /tmp/pt.tgz && \
   docker compose -f docker-compose.prod.yml up -d --build > ~/deploy.log 2>&1'
```

Expected: build completes (~6 min for the frontend; backend rebuilds for `/metrics`); `netdata` + recreated `backend`/`caddy` come up.

- [ ] **Step 2: Confirm containers are up**

Run: `ssh -i ~/.ssh/oracle_paper ubuntu@80.225.219.86 'docker ps --format "{{.Names}}\t{{.Status}}"'`
Expected: 5 containers Up (db/backend/frontend/caddy/**netdata**); db healthy.

- [ ] **Step 3: Confirm `/metrics` is internal-only and scraped**

```bash
# Public must NOT expose metrics (routed to frontend → 404/HTML, never prometheus text):
curl -s https://trader-abhi.duckdns.org/metrics | head -1
# Internal scrape works:
ssh -i ~/.ssh/oracle_paper ubuntu@80.225.219.86 \
  'docker exec delta-paper-trader-netdata-1 curl -s http://backend:8010/metrics | grep app_feed_fresh'
```
Expected: public call returns HTML/404 (NOT `app_feed_fresh`); internal exec prints the `app_feed_fresh` gauge line.

- [ ] **Step 4: Verify Caddy is writing JSON logs and web_log parses them**

```bash
ssh -i ~/.ssh/oracle_paper ubuntu@80.225.219.86 \
  'docker exec delta-paper-trader-caddy-1 sh -c "tail -1 /var/log/caddy/access.log"'
```
Expected: a JSON line with `status`, `duration`, `request`. Compare its field names against `deploy/netdata/go.d/web_log.conf` `mapping:`; if they differ, fix the mapping, re-commit, redeploy. Then in the dashboard (Step 6) confirm a `web_log caddy` chart appears.

- [ ] **Step 5: Open the dashboard via SSH tunnel and verify collectors + chart ids**

```bash
ssh -i ~/.ssh/oracle_paper -L 19999:localhost:19999 ubuntu@80.225.219.86
# then browse http://localhost:19999
```
Expected: charts for system, docker containers (all 5), `httpcheck health`/`httpcheck api_feed`, `web_log caddy`, and a `prometheus paper_backend` section with `app_feed_fresh`. **Note the exact chart ids** for the prometheus + httpcheck + web_log charts and reconcile them with the `on:` lines in `deploy/netdata/health.d/paper_trader.conf` — correct any mismatch, re-commit, redeploy, and reload health (`docker exec delta-paper-trader-netdata-1 netdatacli reload-health`).

- [ ] **Step 6: Prove a Slack alert end-to-end**

```bash
# Send a test notification through the configured Slack path:
ssh -i ~/.ssh/oracle_paper ubuntu@80.225.219.86 \
  'docker exec delta-paper-trader-netdata-1 bash /usr/libexec/netdata/plugins.d/alarm-notify.sh test'
```
Expected: test WARNING/CRITICAL/CLEAR messages arrive in the Slack channel. If not, check `docker logs delta-paper-trader-netdata-1` for the notifier error and confirm `SLACK_WEBHOOK_URL` is set in the container (`docker exec … env | grep SLACK`).

- [ ] **Step 7: Prove the feed-stale alarm fires** (optional but recommended)

Temporarily lower the trigger to confirm wiring: in `paper_feed_stale` set `warn: $this < 2` (so the gauge value of 1 trips it), redeploy config + `netdatacli reload-health`, confirm a Slack alert arrives, then revert to `< 1` and redeploy. This proves the metric→alarm→Slack chain without waiting for a real outage.

---

## Task 7: Documentation

**Files:**
- Create: `docs/MONITORING.md`
- Modify: `docs/HANDOFF.md`

- [ ] **Step 1: Write the runbook** — create `docs/MONITORING.md` covering: what's monitored (system/containers/web_log/httpcheck/APM), how to open the dashboard (SSH tunnel command), the alarm catalogue + thresholds, how to change a threshold (edit `deploy/netdata/health.d/paper_trader.conf` → redeploy → `netdatacli reload-health`), the `SLACK_WEBHOOK_URL` location (VM `.env`, never repo), the mem cap, and the `/metrics` internal-only invariant.

- [ ] **Step 2: Link it from `HANDOFF.md`** — in the "LATEST SESSION" area add a short bullet: "Prod monitoring: Netdata + Slack alerts + backend `/metrics` APM — see `docs/MONITORING.md`."

- [ ] **Step 3: Commit**

```bash
git add docs/MONITORING.md docs/HANDOFF.md
git commit -m "docs(monitoring): Netdata/Slack runbook + handoff pointer"
```

---

## Task 8: Open the PR

- [ ] **Step 1: Push and open the PR**

```bash
git push -u origin feat/prod-monitoring
gh pr create --title "Production monitoring: Netdata + Slack + backend /metrics APM" \
  --body "On-box monitoring for the live VM. Netdata agent (system/containers/Caddy web_log/httpcheck) + backend Prometheus /metrics APM (feed-fresh + per-route latency) + Slack alarms. Mem-capped (250m) for the 1GB box; /metrics is internal-only. Verified on the VM (containers up, internal scrape, web_log parsing, Slack test alert). Paper-only/read-only invariants untouched."
```

Expected: PR created against `main`.

---

## Task 9: Postgres deep monitoring (DB bottlenecks)

**Files:**
- Create: `deploy/netdata/go.d/postgres.conf`
- Create: `deploy/netdata/postgres-monitor-role.sql`
- Create: `deploy/netdata/health.d/postgres.conf`
- Modify: `docker-compose.prod.yml` (netdata env: `NETDATA_PG_PASSWORD`)
- Modify: `.env.prod.example`
- Modify: `docs/MONITORING.md`

Goal: monitor the database itself (not just its container) for bottlenecks — connection-pool
saturation, deadlocks, lock waits, cache-hit ratio, transaction/query durations — via Netdata's
Postgres collector using a least-privilege read-only role. Query-level RCA (`pg_stat_statements`)
is documented as an opt-in (needs a DB restart).

- [ ] **Step 1: Collector job** — create `deploy/netdata/go.d/postgres.conf`:

```yaml
# Deep Postgres/TimescaleDB metrics: connections, locks, deadlocks, cache-hit ratio,
# transaction & query durations, table/index stats, and (when pg_stat_statements is
# enabled) top queries. Connects with a least-privilege pg_monitor role. The password
# is injected from the NETDATA_PG_PASSWORD container env (go.d expands ${VAR}).
jobs:
  - name: paper_db
    dsn: 'postgres://netdata:${NETDATA_PG_PASSWORD}@db:5432/paper_trader'
    timeout: 5
```

- [ ] **Step 2: Least-privilege role** — create `deploy/netdata/postgres-monitor-role.sql`:

```sql
-- Read-only monitoring role for Netdata's postgres collector (least privilege:
-- pg_monitor grants read access to pg_stat_* views/functions only, NOT app table data).
-- Run ONCE on the VM, password matching NETDATA_PG_PASSWORD in the VM .env:
--   docker exec -i delta-paper-trader-db-1 \
--     psql -U paper -d paper_trader -v pw="'YOUR_PASSWORD'" \
--     -f - < deploy/netdata/postgres-monitor-role.sql
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'netdata') THEN
    CREATE ROLE netdata LOGIN;
  END IF;
END $$;
ALTER ROLE netdata PASSWORD :pw;
GRANT pg_monitor TO netdata;
```

- [ ] **Step 3: DB alarms** — create `deploy/netdata/health.d/postgres.conf` (chart ids verified at
deploy, like the other alarms):

```ini
# Connection-pool saturation — running out of connections is a classic bottleneck.
 template: paper_pg_connections
       on: postgres.connections_utilization
   lookup: average -1m unaligned
    units: %
    every: 30s
     warn: $this > 80
     crit: $this > 90
    delay: up 60s down 120s
     info: Postgres connection utilization high — possible pool exhaustion
       to: sysadmin

# Deadlocks indicate lock contention / a bug.
 template: paper_pg_deadlocks
       on: postgres.deadlocks_rate
   lookup: sum -5m unaligned
    units: deadlocks
    every: 1m
     warn: $this > 0
    delay: up 0s down 300s
     info: Postgres deadlocks in the last 5 minutes
       to: sysadmin

# A long-running transaction blocks vacuum and can hold locks.
 template: paper_pg_long_txn
       on: postgres.query_duration
   lookup: max -1m unaligned
    units: seconds
    every: 30s
     warn: $this > 300
    delay: up 60s down 60s
     info: A Postgres transaction/query has been running > 5 minutes
       to: sysadmin
```

- [ ] **Step 4: Wire the env var** — in `docker-compose.prod.yml`, add to the `netdata` service
`environment:` block (next to `SLACK_WEBHOOK_URL`):

```yaml
      NETDATA_PG_PASSWORD: ${NETDATA_PG_PASSWORD:?set NETDATA_PG_PASSWORD in .env}
```

And append to `.env.prod.example`:

```sh

# Password for the least-privilege `netdata` Postgres monitoring role (server-side only).
NETDATA_PG_PASSWORD=change-me-monitoring-password
```

- [ ] **Step 5: Document** — add a "Database monitoring" section to `docs/MONITORING.md`: what's
collected (connections/locks/deadlocks/cache-hit/durations), the one-time role-setup command (Step 2
header), the alarm list, and an **opt-in** note that `pg_stat_statements` (top-query RCA) requires
adding it to the db's `shared_preload_libraries` + `CREATE EXTENSION pg_stat_statements;` + a **DB
restart**, so it's left off by default.

- [ ] **Step 6: Validate + commit**

```bash
# YAML well-formed:
python -c "import yaml; yaml.safe_load(open('deploy/netdata/go.d/postgres.conf'))"
git add deploy/netdata/go.d/postgres.conf deploy/netdata/postgres-monitor-role.sql \
        deploy/netdata/health.d/postgres.conf docker-compose.prod.yml .env.prod.example docs/MONITORING.md
git commit -m "feat(monitoring): Postgres deep metrics (connections/locks/deadlocks/durations) + least-priv role"
```

- [ ] **Step 7: Deploy-time verification** (folds into Task 6): after creating the role on the VM and
redeploying, confirm a `postgres paper_db` section appears on the dashboard with connections/locks/
cache-hit charts, then reconcile the three alarm `on:` chart ids and `netdatacli reload-health`.

---

## Self-review notes (author)
- **Spec coverage:** Netdata service (Task 5) · backend APM `/metrics` (Task 2) · Caddy web_log/latency (Task 3) · httpcheck probes + feed-fresh + 5xx + resource alarms (Task 4) · Slack delivery (Tasks 4/6) · mem cap + on-box + internal-only `/metrics` (Tasks 2/5/6) · docs/runbook (Task 7). All spec sections map to a task.
- **Deferred-by-design (flagged, not placeholders):** exact Netdata chart ids for custom alarms and the web_log JSON field mapping are verified against the live agent in Task 6 (Steps 4–5) — they cannot be known without the running collector; the configs ship a best-effort value plus an explicit reconcile-and-redeploy step. Latency p95 alarm intentionally omitted until a baseline exists (per spec).
- **Type/name consistency:** `render_metrics`, `PrometheusMiddleware`, gauge names (`app_feed_fresh`, `app_feed_age_seconds`, `app_open_positions`), and `http_request_duration_seconds` are used identically across Task 2 code, tests, and the Task 4 alarm `on:` references.
