# Monitoring Runbook — Delta Options Paper-Trading Platform

_Covers the on-box monitoring stack for the live Oracle VM deployment at `https://trader-abhi.duckdns.org`._

---

## Overview

The production stack runs a single **Netdata** container (added to `docker-compose.prod.yml`) alongside the app's own Prometheus **`/metrics`** APM endpoint. Slack webhooks deliver critical alerts. Everything stays on the VM — no third-party telemetry, no external SaaS.

**Why this design for a 1 GB-RAM box:**

- Netdata is memory-capped at 250 MB (`mem_limit: 250m`) so it can never starve the app.
- Netdata's ML/anomaly engine is disabled (`[ml] enabled = no`) to keep steady-state RAM in the 80–120 MB range.
- `/metrics` is never exposed to the public internet (Caddy does not route it).

---

## What's Monitored

### System

CPU, RAM, swap, disk usage/IO, and system load — collected via read-only mounts of the host's `/proc` and `/sys` into the Netdata container.

### Containers

Per-container CPU, memory, and restart counts for all five services: **db / backend / frontend / caddy / netdata** — collected via the `/var/run/docker.sock` mount.

### API Performance (real traffic)

Caddy emits a JSON access log to the shared `caddy_logs` Docker volume. Netdata's `web_log` collector parses it to produce:

- Request rate (req/s)
- Response-time percentiles (p50, p95, p99)
- Status-code split: 2xx / 4xx / 5xx

Configuration: `deploy/netdata/go.d/web_log.conf`

> **Note on first deploy:** verify that the JSON field names in this config match Caddy's actual log output. If Netdata reports zero web-log metrics, inspect a raw log line from the volume and update the field mapping, then `netdatacli reload-health`.

### Synthetic Probes

Netdata's `httpcheck` plugin runs two probes on a short interval:

| Probe | URL | Assert |
|-------|-----|--------|
| Health | `http://backend:8010/health` | HTTP 200 |
| Feed freshness | `http://backend:8010/api/feed` | HTTP 200 and `"fresh":true` in body |

Configuration: `deploy/netdata/go.d/httpcheck.conf`

### App APM (`/metrics`)

The backend exposes a Prometheus endpoint at `GET /metrics` (port 8010). Netdata scrapes it over the internal Docker network. Metrics exposed:

| Metric | Type | Description |
|--------|------|-------------|
| `app_feed_fresh` | Gauge | 1 = Delta feed is live and fresh; 0 = stale/disconnected |
| `app_feed_age_seconds` | Gauge | Seconds since last message from Delta WebSocket |
| `app_open_positions` | Gauge | Count of currently open paper-trade positions |
| `http_request_duration_seconds` | Histogram | Request latency labelled by method, route, and status code |

Configuration: `deploy/netdata/go.d/prometheus.conf`

---

## Database Monitoring

Deep Postgres/TimescaleDB metrics are collected by Netdata's built-in **Postgres go.d collector** (`deploy/netdata/go.d/postgres.conf`), which connects to the `db` service on the internal Docker network using a **least-privilege `netdata` role** (`pg_monitor` grant — read access to `pg_stat_*` views/functions only; no access to application table data).

### What's Collected

| Category | Metrics |
|----------|---------|
| Connections | Active/idle/waiting connections, **connection-pool saturation %** (connections used vs. `max_connections`) |
| Transactions | Commits/s, rollbacks/s |
| Cache | **Cache-hit ratio** (shared buffer hit rate; target > 99%) |
| Locks & contention | **Deadlocks** (rate), **lock waits** |
| Query/transaction age | **Longest-running query and transaction** (seconds) |
| Temp files | Temp-file spills (bytes/count — indicates memory pressure during sorts/hash joins) |
| Table/index sizes | Heap and index sizes per table |
| Scan types | Sequential scans vs. index scans per table (high seq-scan rate on large tables flags a missing index) |

### One-Time Role Setup (Run on the VM)

Create the `netdata` monitoring role before or after first deploy — Netdata will retry the connection on failure:

```bash
docker exec -i delta-paper-trader-db-1 \
  psql -U paper -d paper_trader -v pw="'YOUR_PASSWORD'" \
  -f - < deploy/netdata/postgres-monitor-role.sql
```

The password must match `NETDATA_PG_PASSWORD` in the VM's root `.env` file. The SQL script (`deploy/netdata/postgres-monitor-role.sql`) is idempotent — it skips `CREATE ROLE` if `netdata` already exists.

### DB Alarms

Defined in `deploy/netdata/health.d/postgres.conf`:

| Alarm | Chart | Condition | Severity |
|-------|-------|-----------|----------|
| `paper_pg_connections` | `postgres.connections_utilization` | avg over 1 min > **80%** | Warning |
| `paper_pg_connections` | `postgres.connections_utilization` | avg over 1 min > **90%** | Critical |
| `paper_pg_deadlocks` | `postgres.deadlocks_rate` | sum over 5 min > **0** | Warning |
| `paper_pg_long_txn` | `postgres.query_duration` | max over 1 min > **300 s** (5 min) | Warning |

### Optional: Query-Level RCA via `pg_stat_statements`

When enabled, Netdata's collector also surfaces per-query execution counts, mean/max durations, rows, and cache-hit ratios — useful for identifying slow queries.

**This is OFF by default** because it requires a DB restart. To enable:

1. Edit the TimescaleDB container's PostgreSQL config to add `pg_stat_statements` to `shared_preload_libraries` (e.g. via a custom `postgresql.conf` or `command:` override in `docker-compose.prod.yml`).
2. Connect to the DB and run: `CREATE EXTENSION IF NOT EXISTS pg_stat_statements;`
3. **Restart the `db` container** (`docker compose -f docker-compose.prod.yml restart db`).
4. No change to `deploy/netdata/go.d/postgres.conf` is needed — the collector auto-detects the extension.

---

## Security Invariants

These must not be changed without a deliberate security review:

1. **`/metrics` is internal-only.** It is not present in the Caddy route map (`Caddyfile`), so it is unreachable from the public internet. Only Netdata scrapes it over the internal Docker network.

2. **Netdata dashboard is localhost-only.** Port `19999` is bound to `127.0.0.1` on the VM — it is never published to the public network. Access it exclusively via SSH tunnel (see below).

3. **`SLACK_WEBHOOK_URL` is not in the repo.** It lives only in the VM's root `.env` file, read at container startup by `docker-compose.prod.yml`. The file `.env.prod.example` carries only a placeholder (`SLACK_WEBHOOK_URL=https://hooks.slack.com/services/…`).

---

## Accessing the Netdata Dashboard

Create an SSH tunnel from your workstation, then open your browser:

```bash
ssh -i ~/.ssh/oracle_paper -L 19999:localhost:19999 ubuntu@80.225.219.86
# then open http://localhost:19999 in a browser
```

Keep the tunnel open while browsing. The dashboard shows all charts for system, containers, web-log, httpcheck, and Prometheus metrics in real time.

---

## Alerts (Slack)

Netdata's health engine posts to Slack via `deploy/netdata/health_alarm_notify.conf` (`SEND_SLACK=YES`, webhook URL from `${SLACK_WEBHOOK_URL}`).

### Custom Alarms

Defined in `deploy/netdata/health.d/paper_trader.conf`:

| Alarm | Condition | Severity | Rationale |
|-------|-----------|----------|-----------|
| `paper_feed_stale` | `app_feed_fresh < 1` | **Critical** | The Delta feed is stale — auto-exit is suspended on stale marks, so this is the core trading-risk signal. |
| `paper_backend_down` | `/health` httpcheck probe failing | **Critical** | Backend unreachable; no MTM, no auto-exit, no trade placement. |
| `paper_5xx_rate` | 5xx responses at the edge (Caddy web-log) in the last 5 min > threshold | **Warning** | Elevated server errors reaching users. |

### Stock Alarms

Netdata's built-in alarms also fire for: disk space/IO, RAM, swap, CPU saturation, and container health (OOM kills, repeated restarts).

---

## Changing an Alarm Threshold

1. Edit `deploy/netdata/health.d/paper_trader.conf` on your workstation.
2. Deploy the updated repo to the VM (see `docs/DEPLOY.md` for the archive+scp workflow).
3. Reload health rules without a full container restart:

```bash
docker exec delta-paper-trader-netdata-1 netdatacli reload-health
```

> Note: verify that the `on:` chart IDs in the alarm definitions match the exact Netdata chart IDs visible in your dashboard after first deploy. If a chart ID has changed, update the conf and reload.

---

## Setup Prerequisite (One-Time)

Create a Slack incoming webhook in your Slack workspace and add it to the VM's root `.env`:

```bash
echo 'SLACK_WEBHOOK_URL=https://hooks.slack.com/services/…' >> ~/delta-paper-trader/.env
```

Then bring the full stack up (including Netdata):

```bash
docker compose -f docker-compose.prod.yml up -d --build
```

---

## Deploy-Time Verification Checklist

After the first deploy, confirm the following before relying on alerts:

**1. Web-log field mapping**

Open the Netdata dashboard and check that the `web_log` charts show non-zero request rates. If they are empty, pull a raw log line from the `caddy_logs` volume and compare field names against `deploy/netdata/go.d/web_log.conf`. Update the config, redeploy, and `netdatacli reload-health`.

**2. Alarm chart IDs**

Navigate to the Netdata health configuration page (or use the dashboard search) to find the exact chart IDs for the `httpcheck` and `prometheus` metrics. Cross-check them against the `on:` lines in `deploy/netdata/health.d/paper_trader.conf`. Correct any mismatches, redeploy, and reload health.

**3. End-to-end Slack test**

Send a test alert to confirm the webhook is wired correctly:

```bash
docker exec delta-paper-trader-netdata-1 bash /usr/libexec/netdata/plugins.d/alarm-notify.sh test
```

A test message should appear in the configured Slack channel within a few seconds.

---

## Resource Note

Netdata is hard-capped at `mem_limit: 250m` in `docker-compose.prod.yml`. With ML/anomaly detection disabled, expected steady-state RAM usage is **80–120 MB**, leaving the remaining ~750 MB of the VM's 1 GB for the app stack (db + backend + frontend + Caddy).
