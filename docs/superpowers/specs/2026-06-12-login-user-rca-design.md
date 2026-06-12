# Design: Login / User RCA — auth + error event stream with an admin lookup

_Date: 2026-06-12 · Status: approved-in-principle (pending written-spec review) · Thread 2 of the monitoring sub-projects_

## Goal
When a user reports a problem ("I can't log in", "it errored"), the admin should be able to look up
**that specific user** and see **what happened and why** — failed logins (and the reason), successful
logins (last-seen/history), backend errors they hit, and slow requests — in one timeline. Today this
is impossible: the `logs` table is FK-bound to a provisioned user, and **auth failures are never
recorded** (`_user_from_token` silently raises 401/403).

## Decisions (locked with user)
- **Store: admin panel over Postgres** (no Loki / no new infra) — chosen for a small user base.
- **Capture:** auth **failures** + auth **successes** + backend **5xx/unhandled errors** + **slow
  requests** (over a latency threshold).
- **Retention:** **90 days**, auto-pruned (the table holds email + IP → cap it, don't hoard PII).
- Read-only / paper-only invariants untouched; this is observe-only.

## Non-goals (YAGNI)
- No log-search engine (Loki/ELK). No per-request full-body capture. No tracing spans.
- No change to the existing per-user `logs` (trading activity) table — the RCA view *reads* it
  alongside the new stream, but doesn't replace it.

---

## Architecture

### 1. New table `app_events` (Alembic migration)
Deliberately **NOT FK-bound to `users`** (an auth failure may be for an email with no local user).

| column | type | notes |
|---|---|---|
| `id` | UUID PK | |
| `t` | timestamptz | event time |
| `category` | String(8) | `auth` \| `error` \| `perf` |
| `level` | String(8) | `info` \| `warn` \| `error` |
| `email` | String, nullable | lowercased actor email (the RCA lookup key); may be null |
| `user_id` | UUID, nullable | set when the user is resolved; **no FK** (preserve rows past user deletion) |
| `ip` | String, nullable | client IP |
| `request_id` | String, nullable | correlation id (also returned as `X-Request-ID`) |
| `action` | String(40) | short code, e.g. `login_ok`, `login_not_allowed`, `http_error`, `slow_request` |
| `detail` | Text | reason / path / status / duration / exception summary |

Indexes: `(email, t desc)`, `(category, t desc)`, `(t)`. Plain table (low volume) — not a hypertable.

### 2. `app/obs/events.py` — the emit layer
- **`classify_auth(outcome) -> AuthEvent`** and small pure mappers: given an auth outcome
  (`ok` / `invalid_token` / `not_allowed` / `disabled` / `missing_claims`), return the
  `(action, level, detail_template)`. **Pure → unit-tested.**
- **`async record_event(*, category, level, action, detail, email=None, user_id=None, ip=None, request_id=None) -> None`** —
  opens its **own** `SessionLocal()` and commits **independently** of the request transaction. Critical:
  auth failures raise `HTTPException` and the request txn won't commit, so audit writes must not ride
  on it. Best-effort: swallow + log on its own failure (never break a request because audit logging
  failed).

### 3. `app/obs/middleware.py` — request context + slow-request capture
- **`RequestContextMiddleware`**: generate a `request_id`, stash on `request.state.request_id`, set the
  `X-Request-ID` response header. After `call_next`, if `duration > SLOW_REQUEST_MS` (config, default
  1000 ms) record a `perf`/`warn` `slow_request` event with path + duration + `request.state.user_email`
  (if the auth dep resolved one). Added inside CORS, outside the Prometheus middleware.

### 4. Error capture (`main.py`)
- `app.add_exception_handler(Exception, handler)` — on an unhandled exception, `record_event(category="error",
  level="error", action="http_error", detail=<path + exc type + message>, request_id=..., email=request.state.user_email)`,
  then return a 500 (don't leak internals to the client). Correlates with the Caddy access log + Netdata via `request_id`.

### 5. Auth emission (`app/auth/deps.py`)
- `_user_from_token` gains an optional context (`ip`, `request_id`) and **emits an event on every exit**:
  success → `login_ok` (info); each failure branch → its classified action/level **before** raising.
  Also stash `request.state.user_email`/`user_id` on success so the middleware/error-handler can attach
  it. WS path (`resolve_ws_user`) passes its handshake context too.
- **Throttle (volume guard):** the backend is stateless JWT — every request carries the token, so there
  is no distinct "login" call. To avoid a row per request, an in-memory `AuthThrottle` emits `login_ok`
  at most **once per email per `auth_login_ok_throttle_s` (default 1800s)** and collapses identical
  repeated failures to **once per `auth_fail_throttle_s` (default 60s)**. The throttle is a pure,
  time-injectable unit (unit-tested); it bounds volume without a DB read on the hot path.

### 6. Admin API (`app/api/admin.py`)
- `GET /api/admin/events?email=&category=&level=&since=&limit=` (gated by `require_admin`): filtered,
  `t desc`, default `limit=200`. Returns the event rows (camelCase, epoch-ms).
- Keep existing `/users` and `/users/{id}/logs`.

### 7. Frontend — extend the existing `/admin` page
- An **RCA panel**: email search box + filters (category / level / since) → a unified, color-by-level
  timeline. A default "recent auth failures" view so problems surface without searching. Reuses the
  existing admin fetch/auth pattern.

### 8. Retention prune
- A lightweight periodic task (started in `main.py` lifespan, runs ~every 6h):
  `DELETE FROM app_events WHERE t < now() - interval '90 days'`.

---

## Testing & Definition of Done (per CLAUDE.md, honest about the repo's harness)
- **The repo has no DB test harness** (no conftest, no test DB; all current tests are pure or
  TestClient-no-lifespan). So:
  - **Unit-tested (pure, follows existing pattern):** `classify_auth` and the event mappers
    (each auth outcome → correct `action`/`level`/`detail`); the slow-request threshold decision;
    the admin query-param → filter construction (pure builder), if extracted.
  - **Integration-verified (manual, against the dockerized dev DB — same approach used to validate the
    server engine):** `record_event` persists with its own txn even when the request 401s; a forced
    bad-token request creates a `login_invalid_token` row; `/api/admin/events?email=` returns it; an
    unhandled error creates an `http_error` row with a matching `X-Request-ID`. Documented as a runbook
    check in `docs/MONITORING.md`.
  - This boundary is a conscious match to the existing codebase. (Standing up a Postgres test fixture is
    a separate, larger investment — out of scope here; noted as a future improvement.)
- `tsc`/eslint clean for the frontend panel; `mypy`/`ruff` clean for new backend modules.
- Docs: `docs/MONITORING.md` gains an "RCA / user lookup" section; ARCHITECTURE.md notes the new table.
- Security: admin-gated; no secret in events; emit layer never breaks a request; per-user isolation
  preserved (only `require_admin` can read cross-user events).

## Risks / open items
- **PII:** events hold email + IP. Mitigated by 90-day prune + admin-only access. (If you ever go fully
  public/commercial, revisit with a privacy policy — ties into the roadmap's Phase 1 legal item.)
- **Volume:** slow-request + error events are bounded; auth events are low. If `slow_request` proves
  noisy, raise `SLOW_REQUEST_MS` (single config knob).
- **Emit-on-failure correctness:** the independent-session write is the subtle part — covered by the
  integration check above.
