# Login / User RCA Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the admin a way to look up a specific user and see what happened — failed/successful logins, backend errors they hit, and slow requests — via a new `app_events` stream and an `/admin` RCA panel.

**Architecture:** A new `app_events` Postgres table (not user-FK-bound) is written by an independent-session emit layer (`app/obs/events.py`) so audit rows survive request aborts (401/500). Auth events come from the single choke point `_user_from_token`; errors from a global exception handler; slow requests from a request-context middleware that also assigns `X-Request-ID`. An admin API + `/admin` panel query the stream. A 90-day prune runs in the app lifespan.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 async + Alembic, `prometheus_client` (already present), Next.js/TS admin page.

**Testing boundary (repo has no DB test harness):** pure logic (`classify_auth`, `is_slow`, `AuthThrottle`) is unit-tested; DB persistence + admin query are integration-verified against the dockerized dev DB (documented runbook), matching how the existing server engine was validated.

---

## File map
- Modify `backend/app/db/models.py` — add `AppEvent` model.
- Create Alembic migration for `app_events` (+ indexes).
- Create `backend/app/obs/__init__.py`, `backend/app/obs/events.py` — pure classifiers + `AuthThrottle` + `record_event`.
- Create `backend/app/obs/middleware.py` — `RequestContextMiddleware`.
- Modify `backend/app/config.py` — `slow_request_ms`, `auth_login_ok_throttle_s`, `auth_fail_throttle_s`.
- Modify `backend/app/main.py` — wire middleware, exception handler, prune task.
- Modify `backend/app/auth/deps.py` — emit auth events + stash `request.state`.
- Modify `backend/app/api/admin.py` — `GET /api/admin/events`.
- Create `backend/tests/test_obs_events.py` — pure unit tests.
- Modify `frontend/src/lib/api.ts` + `frontend/src/lib/types.ts` — `getAdminEvents` + `AppEvent` type.
- Modify `frontend/src/app/(terminal)/admin/page.tsx` — RCA panel.
- Modify `docs/MONITORING.md` — RCA section + integration-verify runbook.

---

## Task 1: `AppEvent` model + migration

**Files:** Modify `backend/app/db/models.py`; create a migration.

- [ ] **Step 1: Add the model.** Open `backend/app/db/models.py`, look at the existing `Log` model (around line 197) to match imports/style (`Mapped`, `mapped_column`, `UUID`, `String`, `Text`, `DateTime`, `_uuid`, `Index`). If `Index` isn't already imported from `sqlalchemy`, add it. Add after the `Log` class:

```python
class AppEvent(Base):
    """Observability/RCA event stream — auth, error and perf events. NOT FK-bound to
    users (a failed login may be for an email with no local user); `user_id` is set when
    known but carries no FK so rows survive user deletion."""

    __tablename__ = "app_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    t: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    category: Mapped[str] = mapped_column(String(8))   # auth | error | perf
    level: Mapped[str] = mapped_column(String(8))      # info | warn | error
    email: Mapped[str | None] = mapped_column(String(320), nullable=True, index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    action: Mapped[str] = mapped_column(String(40))
    detail: Mapped[str] = mapped_column(Text)

    __table_args__ = (
        Index("ix_app_events_email_t", "email", "t"),
        Index("ix_app_events_category_t", "category", "t"),
    )
```

- [ ] **Step 2: Generate the migration.** Ensure the dev DB is up (`docker compose up -d` from repo root). From `backend`:
`uv run alembic revision --autogenerate -m "app_events"`
Then OPEN the generated file under `backend/migrations/versions/` and confirm `op.create_table("app_events", ...)` with the columns + both indexes is present (autogenerate sometimes misses composite indexes — add `op.create_index(...)` by hand if missing).

- [ ] **Step 3: Apply + verify.** `uv run alembic upgrade head`, then:
`docker exec paper_trader_db psql -U paper -d paper_trader -c "\d app_events"`
Expected: table with the 9 columns + the indexes.

- [ ] **Step 4: Commit.**
```bash
git add backend/app/db/models.py backend/migrations/versions/
git commit -m "feat(rca): app_events model + migration"
```

---

## Task 2: Pure event core (TDD)

**Files:** Create `backend/app/obs/__init__.py` (empty), `backend/app/obs/events.py`, `backend/tests/test_obs_events.py`.

- [ ] **Step 1: Write the failing tests** — create `backend/tests/test_obs_events.py`:

```python
"""Pure unit tests for the RCA event core (no DB)."""

from __future__ import annotations

from app.obs.events import AuthThrottle, classify_auth, is_slow


def test_classify_known_auth_outcomes() -> None:
    assert classify_auth("ok") == ("login_ok", "info")
    assert classify_auth("invalid_token") == ("login_invalid_token", "warn")
    assert classify_auth("not_allowed") == ("login_not_allowed", "warn")
    assert classify_auth("disabled") == ("login_disabled", "warn")
    assert classify_auth("missing_token") == ("login_missing_token", "warn")
    assert classify_auth("missing_claims") == ("login_missing_claims", "warn")


def test_classify_unknown_outcome_is_generic_warn() -> None:
    assert classify_auth("weird") == ("login_error", "warn")


def test_is_slow_threshold() -> None:
    assert is_slow(1200.0, 1000) is True
    assert is_slow(1000.0, 1000) is True
    assert is_slow(999.9, 1000) is False


def test_auth_throttle_allows_then_blocks_within_window() -> None:
    th = AuthThrottle(min_interval_s=60.0)
    assert th.allow("a@x.com:login_ok", now=100.0) is True   # first time
    assert th.allow("a@x.com:login_ok", now=130.0) is False  # within 60s
    assert th.allow("a@x.com:login_ok", now=161.0) is True   # window elapsed
    assert th.allow("b@x.com:login_ok", now=130.0) is True   # different key independent
```

- [ ] **Step 2: Run to verify failure** — from `backend`: `uv run pytest tests/test_obs_events.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'app.obs'`).

- [ ] **Step 3: Implement** — create `backend/app/obs/__init__.py` (empty file), then `backend/app/obs/events.py`:

```python
"""Observability event stream — auth/error/perf events for per-user RCA.

`record_event` persists to `app_events` on its OWN session/transaction, so an audit row
survives even when the request transaction aborts (auth 401/403, or a 500). Classification
+ throttling are pure and unit-tested; the DB write is a thin best-effort adapter (verified
against the dockerized dev DB — the repo has no DB test harness).
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

# auth outcome code -> (action, level)
_AUTH_MAP: dict[str, tuple[str, str]] = {
    "ok": ("login_ok", "info"),
    "missing_token": ("login_missing_token", "warn"),
    "invalid_token": ("login_invalid_token", "warn"),
    "missing_claims": ("login_missing_claims", "warn"),
    "not_allowed": ("login_not_allowed", "warn"),
    "disabled": ("login_disabled", "warn"),
}


def classify_auth(outcome: str) -> tuple[str, str]:
    """Map an auth outcome code to (action, level). Unknown -> generic warn."""
    return _AUTH_MAP.get(outcome, ("login_error", "warn"))


def is_slow(duration_ms: float, threshold_ms: int) -> bool:
    """Whether a request duration crosses the slow-request threshold (>=)."""
    return duration_ms >= threshold_ms


class AuthThrottle:
    """In-memory min-interval gate so we don't write an auth row on every request.

    `allow(key, now)` returns True at most once per `min_interval_s` per key. `now` is a
    monotonic seconds value injected by the caller (keeps it pure + testable)."""

    def __init__(self, min_interval_s: float) -> None:
        self._min = min_interval_s
        self._last: dict[str, float] = {}

    def allow(self, key: str, now: float) -> bool:
        last = self._last.get(key)
        if last is not None and now - last < self._min:
            return False
        self._last[key] = now
        return True


async def record_event(
    *,
    category: str,
    level: str,
    action: str,
    detail: str,
    email: str | None = None,
    user_id: uuid.UUID | None = None,
    ip: str | None = None,
    request_id: str | None = None,
) -> None:
    """Persist one event on an independent session. Best-effort: never raises (audit
    logging must not break a request)."""
    from app.db.models import AppEvent
    from app.db.session import SessionLocal

    try:
        async with SessionLocal() as session:
            session.add(
                AppEvent(
                    t=datetime.now(UTC),
                    category=category,
                    level=level,
                    action=action,
                    detail=detail[:2000],
                    email=(email or None),
                    user_id=user_id,
                    ip=ip,
                    request_id=request_id,
                )
            )
            await session.commit()
    except Exception:  # noqa: BLE001 — audit logging must never break a request
        logger.exception("record_event failed (category=%s action=%s)", category, action)
```

- [ ] **Step 4: Run to verify pass** — `uv run pytest tests/test_obs_events.py -v`  → all PASS.
- [ ] **Step 5: Type check** — `uv run mypy app/obs/events.py` → clean.
- [ ] **Step 6: Commit.**
```bash
git add backend/app/obs/__init__.py backend/app/obs/events.py backend/tests/test_obs_events.py
git commit -m "feat(rca): pure event core — classify_auth, is_slow, AuthThrottle, record_event"
```

---

## Task 3: Config knobs

**Files:** Modify `backend/app/config.py`.

- [ ] **Step 1:** In the `Settings` class (after the `rate_limit_per_min` field), add:
```python
    slow_request_ms: int = 1000
    auth_login_ok_throttle_s: int = 1800
    auth_fail_throttle_s: int = 60
    app_events_retention_days: int = 90
```
- [ ] **Step 2: Sanity** — `uv run python -c "from app.config import get_settings; print(get_settings().slow_request_ms)"` → `1000`.
- [ ] **Step 3: Commit.**
```bash
git add backend/app/config.py
git commit -m "feat(rca): config knobs (slow_request_ms, auth throttles, retention)"
```

---

## Task 4: Request-context middleware (request-id + slow capture)

**Files:** Create `backend/app/obs/middleware.py`; modify `backend/app/main.py`.

- [ ] **Step 1: Create `backend/app/obs/middleware.py`:**

```python
"""Assigns a per-request id (also returned as X-Request-ID) and records slow requests."""

from __future__ import annotations

import time
import uuid
from typing import TYPE_CHECKING

from starlette.middleware.base import BaseHTTPMiddleware

from app.obs.events import is_slow, record_event

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from starlette.requests import Request
    from starlette.responses import Response


class RequestContextMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: object, slow_ms: int) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._slow_ms = slow_ms

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        rid = uuid.uuid4().hex
        request.state.request_id = rid
        request.state.user_email = None
        request.state.user_id = None
        start = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        dur_ms = (time.perf_counter() - start) * 1000.0
        if request.url.path != "/metrics" and is_slow(dur_ms, self._slow_ms):
            await record_event(
                category="perf",
                level="warn",
                action="slow_request",
                detail=f"{request.method} {request.url.path} {dur_ms:.0f}ms",
                email=getattr(request.state, "user_email", None),
                user_id=getattr(request.state, "user_id", None),
                ip=request.client.host if request.client else None,
                request_id=rid,
            )
        return response
```

- [ ] **Step 2: Wire it in `main.py`.** Add the import near the other `app.` imports:
```python
from app.obs.middleware import RequestContextMiddleware
```
Add it as the OUTERMOST app middleware (added LAST, after CORS) so `request.state.request_id` exists for everything beneath and the `X-Request-ID` header is always set. After the CORS `app.add_middleware(CORSMiddleware, ...)` block, add:
```python
app.add_middleware(RequestContextMiddleware, slow_ms=settings.slow_request_ms)
```

- [ ] **Step 3: Verify no regressions** — `uv run pytest -q` (full suite green); manually confirm `X-Request-ID` via a test: extend nothing, just run `uv run python -c "from fastapi.testclient import TestClient; from app.main import app; print(TestClient(app).get('/health').headers.get('x-request-id'))"` → prints a 32-char hex string.

- [ ] **Step 4: Commit.**
```bash
git add backend/app/obs/middleware.py backend/app/main.py
git commit -m "feat(rca): request-id + slow-request capture middleware"
```

---

## Task 5: Error capture (exception handler)

**Files:** Modify `backend/app/main.py`.

- [ ] **Step 1:** Add imports if missing: `from fastapi import Request` (add `Request` to the existing fastapi import) and `from fastapi.responses import JSONResponse`, plus `from app.obs.events import record_event`.

- [ ] **Step 2:** After the `app = FastAPI(...)` + router includes, register a generic handler (it does NOT catch `HTTPException` — those keep their normal handling):

```python
@app.exception_handler(Exception)
async def _on_unhandled(request: Request, exc: Exception) -> JSONResponse:
    rid = getattr(request.state, "request_id", None)
    await record_event(
        category="error",
        level="error",
        action="http_error",
        detail=f"{request.method} {request.url.path} {type(exc).__name__}: {exc}",
        email=getattr(request.state, "user_email", None),
        user_id=getattr(request.state, "user_id", None),
        ip=request.client.host if request.client else None,
        request_id=rid,
    )
    return JSONResponse(status_code=500, content={"detail": "internal error", "requestId": rid})
```

- [ ] **Step 3:** `uv run pytest -q` green; `uv run mypy app/main.py app/obs/middleware.py` clean.
- [ ] **Step 4: Commit.**
```bash
git add backend/app/main.py
git commit -m "feat(rca): capture unhandled errors to app_events with request-id"
```

---

## Task 6: Auth event emission

**Files:** Modify `backend/app/auth/deps.py`.

- [ ] **Step 1: Module-level throttles + helper.** At the top of `deps.py` (after imports) add:

```python
from app.config import get_settings as _get_settings  # if get_settings not already imported as such
from app.obs.events import AuthThrottle, classify_auth, record_event

_settings = get_settings()
_login_ok_throttle = AuthThrottle(min_interval_s=float(_settings.auth_login_ok_throttle_s))
_fail_throttle = AuthThrottle(min_interval_s=float(_settings.auth_fail_throttle_s))


async def _emit_auth(outcome: str, *, email: str | None, ip: str | None, request_id: str | None) -> None:
    import time as _time

    action, level = classify_auth(outcome)
    throttle = _login_ok_throttle if outcome == "ok" else _fail_throttle
    if not throttle.allow(f"{email or '-'}:{action}", now=_time.monotonic()):
        return
    await record_event(
        category="auth", level=level, action=action,
        detail=f"auth {outcome} ip={ip or '-'}", email=email, ip=ip, request_id=request_id,
    )
```

(If `get_settings` is already imported in `deps.py`, reuse it — don't double-import.)

- [ ] **Step 2: Emit at each exit of `_user_from_token`.** Change the signature to accept context and add `_emit_auth(...)` calls before each raise / on success. The function becomes (preserve the existing dev-bypass branch — no event needed there):

```python
async def _user_from_token(
    session: AsyncSession,
    token: str | None,
    *,
    ip: str | None = None,
    request_id: str | None = None,
) -> User:
    settings = get_settings()

    if not token:
        if settings.is_dev and settings.allow_dev_no_auth:
            uid = await ensure_stub_user(session)
            user = await session.get(User, uid)
            assert user is not None
            return user
        await _emit_auth("missing_token", email=None, ip=ip, request_id=request_id)
        raise HTTPException(401, "missing bearer token")

    try:
        claims = verify_supabase_token(token)
    except AuthError as e:
        await _emit_auth("invalid_token", email=None, ip=ip, request_id=request_id)
        raise HTTPException(401, f"invalid token: {e}") from e

    sub = claims.get("sub")
    email = (claims.get("email") or "").strip().lower()
    if not sub or not email:
        await _emit_auth("missing_claims", email=email or None, ip=ip, request_id=request_id)
        raise HTTPException(401, "token missing sub/email")

    is_admin_seed = bool(settings.admin_email) and email == settings.admin_email.strip().lower()
    allowed = await session.get(AllowedEmail, email)
    if allowed is None and not is_admin_seed:
        await _emit_auth("not_allowed", email=email, ip=ip, request_id=request_id)
        raise HTTPException(403, "email not on the allowlist")

    user = await get_or_create_user_by_supabase(
        session, supabase_uid=sub, email=email, is_admin=is_admin_seed
    )
    if not user.is_active:
        await _emit_auth("disabled", email=email, ip=ip, request_id=request_id)
        raise HTTPException(403, "account disabled")
    await _emit_auth("ok", email=email, ip=ip, request_id=request_id)
    return user
```

- [ ] **Step 3: Pass context + stash state from the callers.** Update `current_user`, `require_admin`, `require_mfa` to pass `ip`/`request_id` and stash on success. Example for `current_user`:

```python
async def current_user(
    request: Request, session: AsyncSession = Depends(get_session)
) -> uuid.UUID:
    ip = request.client.host if request.client else None
    rid = getattr(request.state, "request_id", None)
    user = await _user_from_token(session, _bearer(request), ip=ip, request_id=rid)
    request.state.user_email = user.email
    request.state.user_id = user.id
    return user.id
```
Do the same `ip`/`rid` passing + (on success) state stash in `require_admin`. For `require_mfa`, pass `ip`/`rid` into its `_user_from_token` call too. For `resolve_ws_user(token, session)` leave context as `None` (WS handshake has no `request.state`) — the existing call still works since the new params default to `None`.

- [ ] **Step 4: Verify** — `uv run pytest -q` green (existing auth tests still pass; the new params are keyword-only with defaults); `uv run mypy app/auth/deps.py` clean.
- [ ] **Step 5: Commit.**
```bash
git add backend/app/auth/deps.py
git commit -m "feat(rca): emit throttled auth events (success + every failure reason)"
```

---

## Task 7: Admin events API + retention prune

**Files:** Modify `backend/app/api/admin.py`, `backend/app/main.py`.

- [ ] **Step 1: Add the query endpoint** to `admin.py` (import `AppEvent` from `app.db.models`, and `Optional`/`datetime` as needed). After `user_logs`:

```python
@router.get("/events")
async def list_events(
    email: str | None = None,
    category: str | None = None,
    level: str | None = None,
    since: int | None = None,   # epoch ms
    limit: int = 200,
    session: AsyncSession = Depends(get_session),
    _admin: uuid.UUID = Depends(require_admin),
) -> list[dict[str, Any]]:
    stmt = select(AppEvent).order_by(AppEvent.t.desc()).limit(min(max(limit, 1), 1000))
    if email:
        stmt = stmt.where(AppEvent.email == email.strip().lower())
    if category:
        stmt = stmt.where(AppEvent.category == category)
    if level:
        stmt = stmt.where(AppEvent.level == level)
    if since is not None:
        stmt = stmt.where(AppEvent.t >= datetime.fromtimestamp(since / 1000, tz=UTC))
    res = await session.execute(stmt)
    return [
        {
            "t": int(e.t.timestamp() * 1000),
            "category": e.category,
            "level": e.level,
            "email": e.email,
            "userId": str(e.user_id) if e.user_id else None,
            "ip": e.ip,
            "requestId": e.request_id,
            "action": e.action,
            "detail": e.detail,
        }
        for e in res.scalars().all()
    ]
```

- [ ] **Step 2: Retention prune in lifespan.** In `main.py`'s `lifespan`, start a background prune task after the ticker starts and cancel it on shutdown. Add a small coroutine:

```python
async def _prune_app_events() -> None:
    from datetime import timedelta
    from sqlalchemy import delete
    from app.db.models import AppEvent
    while True:
        try:
            cutoff = datetime.now(UTC) - timedelta(days=settings.app_events_retention_days)
            async with SessionLocal() as session:
                await session.execute(delete(AppEvent).where(AppEvent.t < cutoff))
                await session.commit()
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(6 * 60 * 60)  # every 6h
```
Start it in lifespan: `app.state.prune_task = asyncio.create_task(_prune_app_events())`; in the `finally`, `app.state.prune_task.cancel()` with `contextlib.suppress(asyncio.CancelledError)` around an `await`.

- [ ] **Step 3: Verify** — `uv run pytest -q` green; `uv run mypy app/api/admin.py app/main.py` clean.
- [ ] **Step 4: Integration-verify against the dev DB** (manual, documented): start backend; `curl -i localhost:8010/api/...` with a bogus `Authorization: Bearer xxx` → expect 401 with an `X-Request-ID`; then `docker exec paper_trader_db psql -U paper -d paper_trader -c "select category,level,action,email from app_events order by t desc limit 5;"` → see a `login_invalid_token` row. Record the steps in `docs/MONITORING.md` (Task 9).
- [ ] **Step 5: Commit.**
```bash
git add backend/app/api/admin.py backend/app/main.py
git commit -m "feat(rca): admin GET /api/admin/events + 90-day retention prune"
```

---

## Task 8: Frontend RCA panel

**Files:** Modify `frontend/src/lib/types.ts`, `frontend/src/lib/api.ts`, `frontend/src/app/(terminal)/admin/page.tsx`.

- [ ] **Step 1: Type** — in `types.ts` add:
```typescript
export type AppEvent = {
  t: number;
  category: "auth" | "error" | "perf";
  level: "info" | "warn" | "error";
  email: string | null;
  userId: string | null;
  ip: string | null;
  requestId: string | null;
  action: string;
  detail: string;
};
```
- [ ] **Step 2: API client** — in `api.ts`, next to the existing `getUserLogs` (around line 539, using the same `authedJson` helper), add:
```typescript
export const getAdminEvents = (params: {
  email?: string; category?: string; level?: string; since?: number; limit?: number;
} = {}) => {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== "") q.set(k, String(v));
  const qs = q.toString();
  return authedJson<AppEvent[]>(`/api/admin/events${qs ? `?${qs}` : ""}`);
};
```
(Match the existing export/import style in the file; import `AppEvent` from `./types` where the other admin types are imported.)

- [ ] **Step 3: Panel** — read `frontend/src/app/(terminal)/admin/page.tsx` to follow its existing data-fetch + table styling (TanStack Query or the project's pattern). Add an **"RCA / User events"** section: an email input + category/level `<select>`s + a "Search" action calling `getAdminEvents`, rendering a table (time · level-colored badge · category · action · email · detail · requestId). On first load (no filters) show the latest events so failures are visible without searching. Reuse the page's existing card/table classes — do NOT add custom CSS to `globals.css` (Tailwind v4 gotcha from HANDOFF; use utility classes).

- [ ] **Step 4: Verify** — from `frontend`: `npx tsc --noEmit` and `npm run lint` → clean (only pre-existing warnings).
- [ ] **Step 5: Commit.**
```bash
git add frontend/src/lib/types.ts frontend/src/lib/api.ts "frontend/src/app/(terminal)/admin/page.tsx"
git commit -m "feat(rca): admin /admin RCA panel — search user events by email/category/level"
```

---

## Task 9: Docs

**Files:** Modify `docs/MONITORING.md`, `docs/ARCHITECTURE.md`.

- [ ] **Step 1:** Add an "## RCA / user lookup" section to `docs/MONITORING.md`: what `app_events` captures (auth success/failure, errors, slow requests), how to use the `/admin` panel (search by email), the `X-Request-ID` correlation with the Caddy access log, the 90-day retention, and the **integration-verify runbook** from Task 7 Step 4 (force a bad-token request → confirm the row).
- [ ] **Step 2:** Add a short note to `docs/ARCHITECTURE.md` data-model section: the new `app_events` table + emit layer (`app/obs/`).
- [ ] **Step 3: Commit.**
```bash
git add docs/MONITORING.md docs/ARCHITECTURE.md
git commit -m "docs(rca): MONITORING runbook + ARCHITECTURE note for app_events"
```

---

## Task 10: PR

- [ ] **Step 1:** `git push -u origin <branch>` then `gh pr create` with a body summarizing: app_events stream, auth/error/slow capture, independent-session emit, admin panel, 90-day prune; note the testing boundary (pure units tested, DB integration-verified) and that it's paper-only/observe-only.

---

## Self-review notes (author)
- **Spec coverage:** app_events table (T1) · pure core + throttle (T2) · config (T3) · slow capture + request-id (T4) · error capture (T5) · auth emission success+failures (T6) · admin query + 90-day prune (T7) · frontend panel (T8) · docs (T9). All spec sections map to a task.
- **Independent-session emit** (the subtle correctness point) is in T2's `record_event` and exercised by T7's integration check.
- **Type/name consistency:** `record_event(category, level, action, detail, email, user_id, ip, request_id)`, `classify_auth -> (action, level)`, `is_slow`, `AuthThrottle.allow(key, now)`, `AppEvent` columns, and `/api/admin/events` fields are used identically across backend tasks and the frontend type.
- **Testing boundary** stated up front and matched to the repo (no DB harness): pure logic unit-tested; persistence/query integration-verified.
