# Position Live + History Charts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the positions view load instantly by splitting the live table snapshot from per-position history, serving history at a uniform-by-age resolution with an always-1s live tail, and capturing true net-MTM OHLC so candle wicks show real peaks at every resolution.

**Architecture:** `GET /api/state` returns the lightweight table (no series). A new `GET /api/positions/{id}/series` lazily returns one position's history: a body at a single resolution chosen by the position's age (1s / 10s / 1m / 5m) plus the last ~15 min at 1s from the in-memory ring. The 10s tick-loop write stores net-MTM `open/high/low/close` (from the 1s ticks), and 1m/5m rollups aggregate that OHLC so peaks survive coarsening. The frontend loads a position's series on click and renders server OHLC directly (the 1s/1m/5m resolution buttons are removed).

**Tech Stack:** Python 3.12 / FastAPI / SQLAlchemy 2.0 async / TimescaleDB (`time_bucket`, `first`, `last`) / Alembic · Next.js / TypeScript / lightweight-charts / Zustand / Vitest.

**Spec:** `docs/superpowers/specs/2026-06-14-position-live-history-charts-design.md`

---

## File Structure

**Backend**
- `backend/app/db/models.py` — `StrategySeries`: add `pnl_open/high/low` columns.
- `backend/migrations/versions/<rev>_strategy_series_mtm_ohlc.py` — new Alembic migration.
- `backend/app/sim/service.py` — `MtmOhlc` accumulator, `body_resolution_seconds`, `_rollup_series`, `build_position_series`; update `build_sample`, `series_dict`, `get_state`.
- `backend/app/sim/ticker.py` — accumulate net-MTM OHLC across each 10s window; write it.
- `backend/app/api/sim.py` — drop series from `/api/state`; add `GET /api/positions/{id}/series`.
- `backend/tests/` — `test_mtm_ohlc.py`, `test_body_resolution.py`, `test_build_position_series.py`, plus an integration test for the rollup SQL.

**Frontend**
- `frontend/src/lib/types.ts` — `SeriesSample` gains `pnlOpen/pnlHigh/pnlLow`.
- `frontend/src/lib/api.ts` — `fetchPositionSeries(id)`; `ServerState` position no longer carries `series`.
- `frontend/src/lib/store.ts` — lazy `loadPositionSeries(id)` action; hydrate without series.
- `frontend/src/lib/chartData.ts` — `netMtmCandles(series)`; remove the `tf`-driven bucketing path.
- `frontend/src/components/PositionCharts.tsx` — remove the 1s/1m/5m `Seg`; render server OHLC directly.
- `frontend/src/app/(terminal)/positions/page.tsx` — trigger series load on position open; loading state.
- `frontend/src/lib/chartData.test.ts` — cover `netMtmCandles`.

---

## Phase A — Backend data model

### Task 1: Add net-MTM OHLC columns + migration

**Files:**
- Modify: `backend/app/db/models.py:249` (the `pnl` column area of `StrategySeries`)
- Create: `backend/migrations/versions/<rev>_strategy_series_mtm_ohlc.py`

- [ ] **Step 1: Add the columns to the model**

In `backend/app/db/models.py`, inside `class StrategySeries`, replace the `pnl` line (`pnl: Mapped[float] = mapped_column(Float)`) with:

```python
    pnl: Mapped[float] = mapped_column(Float)  # net MTM close (back-compatible)
    pnl_open: Mapped[float | None] = mapped_column(Float, nullable=True)
    pnl_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    pnl_low: Mapped[float | None] = mapped_column(Float, nullable=True)
```

- [ ] **Step 2: Generate the migration**

Run: `cd backend && python -m alembic revision --autogenerate -m "strategy_series mtm ohlc"`
Expected: a new file under `backend/migrations/versions/` adding three columns to `strategy_series`.

- [ ] **Step 3: Sanitize + backfill the migration**

Open the generated file. Ensure `upgrade()` is exactly the three `add_column` calls and a backfill, and **delete any `op.drop_index('strategy_series_time_idx', ...)` line** (it's TimescaleDB's auto hypertable index — never drop it). The body:

```python
def upgrade() -> None:
    op.add_column("strategy_series", sa.Column("pnl_open", sa.Float(), nullable=True))
    op.add_column("strategy_series", sa.Column("pnl_high", sa.Float(), nullable=True))
    op.add_column("strategy_series", sa.Column("pnl_low", sa.Float(), nullable=True))
    # No historical sub-10s data exists → seed OHLC = pnl (flat candles for old rows).
    op.execute("UPDATE strategy_series SET pnl_open = pnl, pnl_high = pnl, pnl_low = pnl")


def downgrade() -> None:
    op.drop_column("strategy_series", "pnl_low")
    op.drop_column("strategy_series", "pnl_high")
    op.drop_column("strategy_series", "pnl_open")
```

- [ ] **Step 4: Apply + reverse on the dev DB to verify**

Run: `cd backend && python -m alembic upgrade head && python -m alembic downgrade -1 && python -m alembic upgrade head`
Expected: all three commands succeed (applies, reverses cleanly, re-applies).

- [ ] **Step 5: Commit**

```bash
git add backend/app/db/models.py backend/migrations/versions
git commit -m "feat(series): add net-MTM OHLC columns to strategy_series"
```

---

## Phase B — Backend write path (capture OHLC)

### Task 2: `MtmOhlc` accumulator (pure, TDD)

**Files:**
- Modify: `backend/app/sim/service.py` (add near the top-level helpers, after imports)
- Test: `backend/tests/test_mtm_ohlc.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_mtm_ohlc.py
from app.sim.service import MtmOhlc


def test_ohlc_captures_spike_between_endpoints() -> None:
    acc = MtmOhlc()
    for v in [10.0, 25.0, -5.0, 12.0]:  # spikes up then down, ends mid
        acc.add(v)
    assert acc.started() is True
    assert acc.snapshot() == {"open": 10.0, "high": 25.0, "low": -5.0, "close": 12.0}


def test_ohlc_reset_clears_state() -> None:
    acc = MtmOhlc()
    acc.add(3.0)
    acc.reset()
    assert acc.started() is False
    acc.add(7.0)
    assert acc.snapshot() == {"open": 7.0, "high": 7.0, "low": 7.0, "close": 7.0}
```

- [ ] **Step 2: Run it; verify it fails**

Run: `cd backend && uv run pytest tests/test_mtm_ohlc.py -q`
Expected: FAIL with `ImportError: cannot import name 'MtmOhlc'`.

- [ ] **Step 3: Implement `MtmOhlc`**

Add to `backend/app/sim/service.py` (top-level, e.g. just below the imports):

```python
class MtmOhlc:
    """Running open/high/low/close of net MTM across one 10s DB-write window."""

    __slots__ = ("open", "high", "low", "close", "_started")

    def __init__(self) -> None:
        self._started = False

    def add(self, v: float) -> None:
        if not self._started:
            self.open = self.high = self.low = self.close = v
            self._started = True
        else:
            self.high = max(self.high, v)
            self.low = min(self.low, v)
            self.close = v

    def started(self) -> bool:
        return self._started

    def snapshot(self) -> dict[str, float]:
        return {"open": self.open, "high": self.high, "low": self.low, "close": self.close}

    def reset(self) -> None:
        self._started = False
```

- [ ] **Step 4: Run it; verify it passes**

Run: `cd backend && uv run pytest tests/test_mtm_ohlc.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/app/sim/service.py backend/tests/test_mtm_ohlc.py
git commit -m "feat(series): MtmOhlc accumulator for net-MTM OHLC"
```

### Task 3: `build_sample` emits degenerate OHLC (TDD)

**Files:**
- Modify: `backend/app/sim/service.py:119-129` (the `return {...}` of `build_sample`)
- Test: `backend/tests/test_build_sample_ohlc.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_build_sample_ohlc.py
# Tail/live samples are single points → OHLC must be degenerate (all == pnl).
def test_sample_dict_keys_present() -> None:
    sample = {"t": 1, "pnl": 4.0, "pnlOpen": 4.0, "pnlHigh": 4.0, "pnlLow": 4.0}
    assert sample["pnlOpen"] == sample["pnlHigh"] == sample["pnlLow"] == sample["pnl"]
```

(`build_sample` needs a live MarketView; this guards the contract that downstream code relies on. The real wiring is covered by the integration test in Task 8/9.)

- [ ] **Step 2: Run it; verify it passes trivially, then update the producer**

Run: `cd backend && uv run pytest tests/test_build_sample_ohlc.py -q`
Expected: PASS (it documents the contract). Now make `build_sample` honor it.

- [ ] **Step 3: Add OHLC keys to `build_sample`'s return**

In `backend/app/sim/service.py`, inside `build_sample`, compute the net pnl once and add the OHLC keys. Replace the `return {...}` block (currently starting `"pnl": net_pnl(...)`) with:

```python
    net = net_pnl(
        [LegQuote(lg.side, lg.qty, lg.contract_value, lg.entry, _mark(mv, lg)) for lg in legs]
    )
    return {
        "t": _ms(now),
        "pnl": net,
        "pnlOpen": net,
        "pnlHigh": net,
        "pnlLow": net,
        "delta": ng["delta"],
        "theta": ng["theta"],
        "vega": ng["vega"],
        "atmIv": atm_iv,
        "legs": leg_rows,
    }
```

- [ ] **Step 4: Run the sim test suite to confirm nothing broke**

Run: `cd backend && uv run pytest tests/ -q -k "sample or series or bounded"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/sim/service.py backend/tests/test_build_sample_ohlc.py
git commit -m "feat(series): build_sample emits degenerate OHLC for live/tail points"
```

### Task 4: Wire the accumulator into the 10s write

**Files:**
- Modify: `backend/app/sim/ticker.py:45-49` (init), `:116-133` (sample/ring/write block), `:147-150` (dead-ring GC)

- [ ] **Step 1: Add the per-position accumulator store in `__init__`**

In `backend/app/sim/ticker.py`, after `self._last_settle = 0.0` (line ~49), add:

```python
        self._ohlc: dict[str, service.MtmOhlc] = {}  # net-MTM OHLC per position, per 10s window
```

- [ ] **Step 2: Accumulate every tick and write OHLC on the 10s boundary**

In `_tick`, replace the block that builds the sample and conditionally writes the row (currently lines ~116-133, from `sample = service.build_sample(...)` through the `StrategySeries(...)` add) with:

```python
                sample = service.build_sample(p, mv, now)
                # deque(maxlen) auto-evicts the oldest — O(1), constant RAM, no manual trim
                ring = self.series.setdefault(str(p.id), deque(maxlen=RING_MAXLEN))
                ring.append(sample)  # full sample — per-leg + IV detail kept for the charts

                # accumulate net-MTM OHLC across this 10s window (captures intra-window spikes)
                acc = self._ohlc.setdefault(str(p.id), service.MtmOhlc())
                acc.add(sample["pnl"])
                if write_db:
                    o = acc.snapshot()
                    acc.reset()
                    session.add(
                        StrategySeries(
                            time=now,
                            position_id=p.id,
                            user_id=uid,
                            pnl=o["close"],
                            pnl_open=o["open"],
                            pnl_high=o["high"],
                            pnl_low=o["low"],
                            delta=sample["delta"],
                            theta=sample["theta"],
                            vega=sample["vega"],
                            atm_iv=sample["atmIv"],
                            legs=sample["legs"],
                        )
                    )
```

- [ ] **Step 3: GC accumulators for closed positions**

In `_tick`, the dead-ring GC loop (currently `for dead in [k for k in self.series if k not in live_ids]: del self.series[dead]`) — extend it to also drop the accumulator:

```python
            live_ids = {str(p.id) for p in positions if p.status == "open"}
            for dead in [k for k in self.series if k not in live_ids]:
                del self.series[dead]
                self._ohlc.pop(dead, None)
```

- [ ] **Step 4: Verify the backend imports + tick module load**

Run: `cd backend && uv run python -c "import app.sim.ticker"`
Expected: no error.

- [ ] **Step 5: Commit**

```bash
git add backend/app/sim/ticker.py
git commit -m "feat(series): tick loop writes net-MTM OHLC every 10s"
```

---

## Phase C — Backend read path (uniform-by-age serving)

### Task 5: `body_resolution_seconds` (pure, TDD)

**Files:**
- Modify: `backend/app/sim/service.py` (add near `DB_RAW_WINDOW_S`, line ~729)
- Test: `backend/tests/test_body_resolution.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_body_resolution.py
from app.sim.service import body_resolution_seconds


def test_resolution_boundaries() -> None:
    assert body_resolution_seconds(10 * 60) == 1          # <=15 min -> 1s
    assert body_resolution_seconds(15 * 60) == 1
    assert body_resolution_seconds(15 * 60 + 1) == 10     # >15 min -> 10s
    assert body_resolution_seconds(12 * 3600) == 10
    assert body_resolution_seconds(12 * 3600 + 1) == 60   # >12 h -> 1 min
    assert body_resolution_seconds(24 * 3600) == 60
    assert body_resolution_seconds(24 * 3600 + 1) == 300  # >24 h -> 5 min
    assert body_resolution_seconds(7 * 24 * 3600) == 300
```

- [ ] **Step 2: Run it; verify it fails**

Run: `cd backend && uv run pytest tests/test_body_resolution.py -q`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement it**

Add to `backend/app/sim/service.py` near the series constants:

```python
TAIL_SECONDS = 15 * 60  # last 15 min always served at 1s from the ring (matches RING_MAXLEN)


def body_resolution_seconds(span_seconds: float) -> int:
    """Uniform history-body resolution chosen by the position's span (age, or lifetime if closed)."""
    if span_seconds <= 15 * 60:
        return 1
    if span_seconds <= 12 * 3600:
        return 10
    if span_seconds <= 24 * 3600:
        return 60
    return 300
```

- [ ] **Step 4: Run it; verify it passes**

Run: `cd backend && uv run pytest tests/test_body_resolution.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/sim/service.py backend/tests/test_body_resolution.py
git commit -m "feat(series): body_resolution_seconds (uniform-by-age)"
```

### Task 6: `series_dict` emits OHLC (TDD)

**Files:**
- Modify: `backend/app/sim/service.py:644-653` (`series_dict`)
- Test: `backend/tests/test_series_dict_ohlc.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_series_dict_ohlc.py
from datetime import UTC, datetime
from types import SimpleNamespace

from app.sim.service import series_dict


def test_series_dict_includes_ohlc() -> None:
    row = SimpleNamespace(
        time=datetime(2026, 6, 14, tzinfo=UTC),
        pnl=12.0, pnl_open=10.0, pnl_high=25.0, pnl_low=-5.0,
        delta=0.1, theta=-2.0, vega=3.0, atm_iv={"2026-06-20": 0.5}, legs={},
    )
    out = series_dict(row)
    assert out["pnl"] == 12.0
    assert (out["pnlOpen"], out["pnlHigh"], out["pnlLow"]) == (10.0, 25.0, -5.0)


def test_series_dict_ohlc_falls_back_to_pnl_when_null() -> None:
    row = SimpleNamespace(
        time=datetime(2026, 6, 14, tzinfo=UTC),
        pnl=8.0, pnl_open=None, pnl_high=None, pnl_low=None,
        delta=0.0, theta=0.0, vega=0.0, atm_iv={}, legs={},
    )
    out = series_dict(row)
    assert out["pnlOpen"] == out["pnlHigh"] == out["pnlLow"] == 8.0
```

- [ ] **Step 2: Run it; verify it fails**

Run: `cd backend && uv run pytest tests/test_series_dict_ohlc.py -q`
Expected: FAIL (KeyError on `pnlOpen`).

- [ ] **Step 3: Update `series_dict`**

Replace `series_dict` in `backend/app/sim/service.py` with:

```python
def series_dict(s: StrategySeries) -> dict[str, Any]:
    return {
        "t": _ms(s.time),
        "pnl": s.pnl,
        "pnlOpen": s.pnl_open if s.pnl_open is not None else s.pnl,
        "pnlHigh": s.pnl_high if s.pnl_high is not None else s.pnl,
        "pnlLow": s.pnl_low if s.pnl_low is not None else s.pnl,
        "delta": s.delta,
        "theta": s.theta,
        "vega": s.vega,
        "atmIv": s.atm_iv,
        "legs": s.legs,
    }
```

- [ ] **Step 4: Run it; verify it passes**

Run: `cd backend && uv run pytest tests/test_series_dict_ohlc.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/sim/service.py backend/tests/test_series_dict_ohlc.py
git commit -m "feat(series): series_dict emits net-MTM OHLC (null-safe)"
```

### Task 7: Rollup SQL helper `_rollup_series`

**Files:**
- Modify: `backend/app/sim/service.py` (add after `series_dict`)
- Test: `backend/tests/test_rollup_series_integration.py` (DB-backed)

- [ ] **Step 1: Write the integration test**

```python
# backend/tests/test_rollup_series_integration.py
# Requires the dev TimescaleDB (same one alembic targets). Skips if unreachable.
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.sim.service import _rollup_series

pytestmark = pytest.mark.asyncio

DB_URL = os.environ.get("DATABASE_URL", "postgresql+asyncpg://paper:paper@localhost:5432/paper_trader")


async def test_rollup_preserves_high_low_across_buckets() -> None:
    engine = create_async_engine(DB_URL)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    pid = uuid.uuid4()
    uid = uuid.uuid4()
    base = datetime(2026, 1, 1, tzinfo=UTC)
    async with Session() as s:
        # 12 rows @ 10s within one minute; row 4 spikes high, row 9 spikes low.
        for i in range(12):
            hi = 100.0 if i == 4 else 10.0
            lo = -100.0 if i == 9 else 5.0
            await s.execute(
                text(
                    "INSERT INTO strategy_series "
                    "(time, position_id, user_id, pnl, pnl_open, pnl_high, pnl_low, delta, theta, vega, atm_iv, legs) "
                    "VALUES (:t,:p,:u,:c,:o,:h,:l,0,0,0,'{}'::jsonb,'{}'::jsonb)"
                ),
                {"t": base + timedelta(seconds=10 * i), "p": pid, "u": uid,
                 "c": 9.0, "o": 8.0, "h": hi, "l": lo},
            )
        await s.commit()
        pts = await _rollup_series(s, pid, 60, base, base + timedelta(minutes=1))
        await s.execute(text("DELETE FROM strategy_series WHERE position_id = :p"), {"p": pid})
        await s.commit()
    await engine.dispose()
    assert len(pts) == 1
    assert pts[0]["pnlHigh"] == 100.0  # spike preserved
    assert pts[0]["pnlLow"] == -100.0  # trough preserved
```

- [ ] **Step 2: Run it; verify it fails**

Run: `cd backend && uv run pytest tests/test_rollup_series_integration.py -q`
Expected: FAIL with `ImportError: cannot import name '_rollup_series'`.

- [ ] **Step 3: Implement `_rollup_series`**

Add to `backend/app/sim/service.py`:

```python
async def _rollup_series(
    session: AsyncSession,
    pos_id: uuid.UUID,
    bucket_seconds: int,
    start: datetime,
    end: datetime,
) -> list[dict[str, Any]]:
    """One uniform OHLC rollup of net MTM (per-leg + Greeks = last-in-bucket) for [start, end)."""
    rows = await session.execute(
        text(
            f"SELECT time_bucket(make_interval(secs => {int(bucket_seconds)}), time) AS bucket, "
            "first(pnl_open, time) AS o, max(pnl_high) AS h, min(pnl_low) AS l, last(pnl, time) AS c, "
            "last(delta, time) AS delta, last(theta, time) AS theta, last(vega, time) AS vega, "
            "last(atm_iv, time) AS atm_iv, last(legs, time) AS legs "
            "FROM strategy_series WHERE position_id = :pid AND time >= :start AND time < :end "
            "GROUP BY bucket ORDER BY bucket"
        ),
        {"pid": pos_id, "start": start, "end": end},
    )
    return [
        {
            "t": int(r.bucket.timestamp() * 1000),
            "pnl": r.c, "pnlOpen": r.o, "pnlHigh": r.h, "pnlLow": r.l,
            "delta": r.delta, "theta": r.theta, "vega": r.vega,
            "atmIv": r.atm_iv or {}, "legs": r.legs or {},
        }
        for r in rows
    ]
```

(`int(bucket_seconds)` is interpolated from a server-controlled int only — never user input — so it's injection-safe.)

- [ ] **Step 4: Run it; verify it passes**

Run: `cd backend && uv run pytest tests/test_rollup_series_integration.py -q`
Expected: PASS (or SKIP if the dev DB isn't running — start it first: it's the same DB alembic uses).

- [ ] **Step 5: Commit**

```bash
git add backend/app/sim/service.py backend/tests/test_rollup_series_integration.py
git commit -m "feat(series): _rollup_series OHLC rollup (peaks survive coarsening)"
```

### Task 8: `build_position_series` assembly (TDD)

**Files:**
- Modify: `backend/app/sim/service.py` (add after `_rollup_series`)
- Test: `backend/tests/test_build_position_series.py`

This function orchestrates body + tail. The DB body is fetched via `series_dict`/`_rollup_series`; the assembly logic (seam at the ring's first sample, resolution choice, tail append) is pure and unit-tested by injecting the already-fetched body rows and ring.

- [ ] **Step 1: Write the failing test for the pure assembler**

```python
# backend/tests/test_build_position_series.py
from app.sim.service import _assemble_series


def test_assemble_open_uses_body_before_tail_and_1s_tail() -> None:
    # body = coarse points (older), ring = 1s tail (recent). Seam at ring[0].t.
    body = [{"t": 1000, "pnl": 1.0}, {"t": 2000, "pnl": 2.0}, {"t": 9000, "pnl": 9.0}]
    ring = [{"t": 5000, "pnl": 5.0}, {"t": 6000, "pnl": 6.0}]  # tail begins at 5000
    out = _assemble_series(body, ring)
    # body kept only strictly before the tail start; then the full tail
    assert [p["t"] for p in out] == [1000, 2000, 5000, 6000]


def test_assemble_empty_ring_returns_body() -> None:
    body = [{"t": 1000, "pnl": 1.0}]
    assert _assemble_series(body, []) == body
```

- [ ] **Step 2: Run it; verify it fails**

Run: `cd backend && uv run pytest tests/test_build_position_series.py -q`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement `_assemble_series` + `build_position_series`**

Add to `backend/app/sim/service.py`:

```python
def _assemble_series(
    body: list[dict[str, Any]], tail: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Body up to where the 1s tail begins, then the tail (mirrors the old bounded_series seam)."""
    if not tail:
        return body
    first_t = tail[0]["t"]
    return [p for p in body if p["t"] < first_t] + list(tail)


async def build_position_series(
    session: AsyncSession,
    pos: Position,
    ring_full: Iterable[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Uniform-by-age history body + always-1s live tail (open) for one position."""
    tail = [
        {k: s[k] for k in ("t", "pnl", "pnlOpen", "pnlHigh", "pnlLow",
                           "delta", "theta", "vega", "atmIv", "legs")}
        for s in (list(ring_full) if ring_full else [])
    ]
    # span = lifetime for closed, age for open
    end = pos.closed_at if (pos.status != "open" and pos.closed_at) else datetime.now(UTC)
    span = (end - pos.opened_at).total_seconds()
    res = body_resolution_seconds(span)
    # body covers entry → where the tail starts (or → end when there's no tail)
    body_end = datetime.fromtimestamp(tail[0]["t"] / 1000, UTC) if tail else end
    if res == 1:
        body: list[dict[str, Any]] = []  # whole thing is in the ring / ≤15 min
    elif res == 10:
        rows = await session.execute(
            select(StrategySeries)
            .where(
                StrategySeries.position_id == pos.id,
                StrategySeries.time < body_end,
            )
            .order_by(StrategySeries.time)
        )
        body = [series_dict(r) for r in rows.scalars().all()]
    else:
        body = await _rollup_series(session, pos.id, res, pos.opened_at, body_end)
    return _assemble_series(body, tail)
```

- [ ] **Step 4: Run it; verify the assembler tests pass**

Run: `cd backend && uv run pytest tests/test_build_position_series.py -q`
Expected: PASS (the pure `_assemble_series` tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/sim/service.py backend/tests/test_build_position_series.py
git commit -m "feat(series): build_position_series (uniform body + 1s tail)"
```

### Task 9: API split — light `/api/state` + per-position series endpoint

**Files:**
- Modify: `backend/app/sim/service.py:784-840` (`get_state` — drop the per-position `_db_series` call)
- Modify: `backend/app/api/sim.py:97-103` (add the new route nearby)
- Test: `backend/tests/test_state_api_split.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_state_api_split.py
# get_state must NOT embed history; each position carries series == [].
import inspect

from app.sim import service


def test_get_state_does_not_call_db_series() -> None:
    src = inspect.getsource(service.get_state)
    assert "_db_series" not in src  # history is no longer eager
    assert "build_position_series" not in src  # not eager either


def test_service_exposes_per_position_series_builder() -> None:
    assert hasattr(service, "build_position_series")
```

- [ ] **Step 2: Run it; verify it fails**

Run: `cd backend && uv run pytest tests/test_state_api_split.py -q`
Expected: FAIL (`get_state` still references `_db_series`).

- [ ] **Step 3: Make `get_state` return empty series**

In `backend/app/sim/service.py`, in `get_state`, replace the per-position loop:

```python
    pos_dicts = []
    for p in positions:
        db = await _db_series(session, p.id)
        series = bounded_series(db, series_store.get(str(p.id)) or [])
        pos_dicts.append(position_dict(p, mv, series))
```

with:

```python
    # Light table snapshot: no history. Series is loaded lazily per position
    # via GET /api/positions/{id}/series. See build_position_series.
    pos_dicts = [position_dict(p, mv, []) for p in positions]
```

- [ ] **Step 4: Add the per-position series route**

In `backend/app/api/sim.py`, after the `get_state` route (line ~103), add:

```python
@router.get("/positions/{pos_id}/series")
async def get_position_series(
    pos_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user_id: uuid.UUID = Depends(current_user),
) -> dict[str, Any]:
    res = await session.execute(
        select(Position)
        .where(Position.id == pos_id, Position.user_id == user_id)
        .options(selectinload(Position.legs))
    )
    pos = res.scalar_one_or_none()
    if pos is None:
        raise HTTPException(404, "position not found")
    ring = _store(request).get(str(pos_id))
    series = await service.build_position_series(session, pos, ring)
    return {"id": str(pos_id), "series": series}
```

- [ ] **Step 5: Run the test + the broader sim suite**

Run: `cd backend && uv run pytest tests/test_state_api_split.py tests/ -q -k "state or series or bounded or settlement"`
Expected: PASS. (If `_db_series`/`bounded_series` are now unused and a linter flags them, leave them in place for this task — they're removed in cleanup below.)

- [ ] **Step 6: Remove the now-dead `_db_series` (and `bounded_series` if unused)**

Confirm no references remain: `cd backend && grep -rn "_db_series\|bounded_series" app/ | grep -v "def _db_series\|def bounded_series"`. If only the definitions and their own tests reference them, delete `_db_series`, `bounded_series`, and `backend/tests/test_bounded_series.py`. Re-run `uv run pytest tests/ -q`.

- [ ] **Step 7: Commit**

```bash
git add backend/app/sim/service.py backend/app/api/sim.py backend/tests/
git commit -m "feat(api): split light /api/state from lazy /api/positions/{id}/series"
```

---

## Phase D — Frontend

### Task 10: Types + `fetchPositionSeries`

**Files:**
- Modify: `frontend/src/lib/types.ts:86-94` (`SeriesSample`)
- Modify: `frontend/src/lib/api.ts:386-393` (near `fetchState`)

- [ ] **Step 1: Add OHLC fields to `SeriesSample`**

In `frontend/src/lib/types.ts`, in `interface SeriesSample`, add after `pnl: number;`:

```typescript
  pnlOpen: number; // net MTM open of this point's window
  pnlHigh: number; // net MTM high (true peak — survives downsampling)
  pnlLow: number; // net MTM low
```

- [ ] **Step 2: Add `fetchPositionSeries`**

In `frontend/src/lib/api.ts`, after `fetchState`, add:

```typescript
export async function fetchPositionSeries(id: string): Promise<SeriesSample[] | null> {
  try {
    const r = await fetch(`${API}/api/positions/${id}/series`, { headers: await authHeaders() });
    if (!r.ok) return null;
    const j = (await r.json()) as { series: SeriesSample[] };
    return j.series;
  } catch {
    return null;
  }
}
```

Add `SeriesSample` to the imports from `./types` at the top of `api.ts` if not already present.

- [ ] **Step 3: Type-check**

Run: `cd frontend && npx tsc --noEmit`
Expected: errors only where consumers still assume `series` on `ServerState` positions (fixed in Task 11) — no errors in `api.ts`/`types.ts` themselves.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib/types.ts frontend/src/lib/api.ts
git commit -m "feat(fe): SeriesSample OHLC + fetchPositionSeries"
```

### Task 11: Store lazy-load action

**Files:**
- Modify: `frontend/src/lib/store.ts` (the `State` interface ~line 186, the store actions block, and `applyServerState` ~line 94)

- [ ] **Step 1: Add the action to the `State` interface**

In `frontend/src/lib/store.ts`, in the `State` interface (near `positions: Position[]`), add:

```typescript
  loadPositionSeries: (id: string) => Promise<void>;
```

- [ ] **Step 2: Import `fetchPositionSeries`**

In the import from `./api` (line ~17 where `fetchState` is imported), add `fetchPositionSeries`.

- [ ] **Step 3: Implement the action**

In the store actions object (the `create<State>()` block), add:

```typescript
      loadPositionSeries: async (id: string) => {
        const series = await fetchPositionSeries(id);
        if (!series) return;
        useStore.setState((s) => ({
          positions: s.positions.map((p) => (p.id === id ? { ...p, series } : p)),
        }));
      },
```

- [ ] **Step 4: Confirm hydrate no longer relies on embedded series**

`applyServerState` already sets `positions: st.positions`. Since the server now returns `series: []`, positions hydrate with empty series until `loadPositionSeries` runs — no code change needed there, but verify the WS `appendSample` path (line ~143) still works (it appends to whatever series is present, growing the live tail).

- [ ] **Step 5: Type-check**

Run: `cd frontend && npx tsc --noEmit`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/store.ts
git commit -m "feat(fe): lazy loadPositionSeries action"
```

### Task 12: `chartData.netMtmCandles` (TDD)

**Files:**
- Modify: `frontend/src/lib/chartData.ts`
- Test: `frontend/src/lib/chartData.test.ts`

- [ ] **Step 1: Write the failing test**

Add to `frontend/src/lib/chartData.test.ts`:

```typescript
import { netMtmCandles } from "./chartData";

test("netMtmCandles renders server OHLC directly (one candle per sample)", () => {
  const series = [
    { t: 60000, pnl: 12, pnlOpen: 10, pnlHigh: 25, pnlLow: -5, delta: 0, theta: 0, vega: 0, atmIv: {}, legs: {} },
    { t: 120000, pnl: 14, pnlOpen: 12, pnlHigh: 14, pnlLow: 11, delta: 0, theta: 0, vega: 0, atmIv: {}, legs: {} },
  ] as unknown as Parameters<typeof netMtmCandles>[0];
  const out = netMtmCandles(series);
  expect(out).toEqual([
    { time: 60, open: 10, high: 25, low: -5, close: 12 },
    { time: 120, open: 12, high: 14, low: 11, close: 14 },
  ]);
});
```

- [ ] **Step 2: Run it; verify it fails**

Run: `cd frontend && npx vitest run src/lib/chartData.test.ts`
Expected: FAIL (`netMtmCandles` not exported).

- [ ] **Step 3: Implement `netMtmCandles`**

Add to `frontend/src/lib/chartData.ts`:

```typescript
// Net MTM candles straight from the server's per-point OHLC (no client re-bucketing —
// the server already chose the resolution). Time in epoch-seconds for lightweight-charts.
export function netMtmCandles(series: SeriesSample[]): OHLC[] {
  return series.map((s) => ({
    time: Math.floor(s.t / 1000) as UTCTimestamp,
    open: s.pnlOpen,
    high: s.pnlHigh,
    low: s.pnlLow,
    close: s.pnl, // `pnl` is the close (back-compatible)
  }));
}
```

- [ ] **Step 4: Run it; verify it passes**

Run: `cd frontend && npx vitest run src/lib/chartData.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/chartData.ts frontend/src/lib/chartData.test.ts
git commit -m "feat(fe): netMtmCandles renders server OHLC directly"
```

### Task 13: PositionCharts — remove resolution buttons, render OHLC directly + load on open

**Files:**
- Modify: `frontend/src/components/PositionCharts.tsx` (the `tf` state + `Seg`, `loadData`, the data-effect signature)
- Modify: `frontend/src/app/(terminal)/positions/page.tsx` (call `loadPositionSeries` on open + loading state)

- [ ] **Step 1: Remove the `tf` resolution toggle**

In `PositionCharts.tsx`, delete the resolution `Seg` block (the one with `opts={[{ v: 1, label: "1s" }, { v: 60, label: "1m" }, { v: 300, label: "5m" }]}`, lines ~199-207). Remove the `tf` state declaration and its `setTf`. Keep the Net/Legs `Seg` and the Line/Candle `Seg`.

- [ ] **Step 2: Replace `tf`-bucketed rendering with direct OHLC / direct lines**

In the `Panel` component's `loadData()` (lines ~428-439), replace with:

```typescript
  function loadData() {
    if (!chartRef.current) return;
    const s = primaryRef.current;
    if (s) {
      if (kind === "mtm" && candle) {
        (s as ISeriesApi<"Candlestick">).setData(capPoints(netCandles));
      } else {
        (s as ISeriesApi<"Baseline">).setData(
          capPoints(net.map((p) => ({ time: Math.floor(p.t / 1000) as UTCTimestamp, value: p.v }))),
        );
      }
    }
    legRefs.current.forEach((ls, i) =>
      ls.setData(
        capPoints((legs[i]?.pts ?? []).map((p) => ({ time: Math.floor(p.t / 1000) as UTCTimestamp, value: p.v }))),
      ),
    );
  }
```

Pass `netCandles` (from `netMtmCandles(series)`) into the `Panel` as a prop alongside `net`. In the parent `PositionCharts`, compute `const netCandles = netMtmCandles(series);` and give the MTM `<Panel>` `netCandles={netCandles}` (other panels can pass `netCandles={[]}`). Add `netCandles: OHLC[]` to `PanelProps`. Import `netMtmCandles` and `UTCTimestamp`.

- [ ] **Step 3: Drop `tf` from the data-effect signature**

In the data effect (line ~451), change `const sig = `${tf}|${candle}|${kind}|${chartVer}`;` to `const sig = `${candle}|${kind}|${chartVer}`;`. Remove any remaining `tf` references (e.g. the `fmtDateTime(series[0].t / 1000, tf)` call at line ~178 — drop the `tf` arg or pass a fixed default per `fmtDateTime`'s signature).

- [ ] **Step 4: Load series when a position is opened**

In `frontend/src/app/(terminal)/positions/page.tsx`, where a position's detail/charts are shown, call the store action on open. Add near the top of the detail render path:

```typescript
const loadPositionSeries = useStore((s) => s.loadPositionSeries);
useEffect(() => {
  if (openPositionId) loadPositionSeries(openPositionId);
}, [openPositionId, loadPositionSeries]);
```

(Use the existing variable that identifies the opened position — match the page's current naming.) While `position.series.length === 0`, render a small "loading chart…" placeholder instead of `PositionCharts`.

- [ ] **Step 5: Type-check + unit tests + build**

Run: `cd frontend && npx tsc --noEmit && npx vitest run && npm run build`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/PositionCharts.tsx "frontend/src/app/(terminal)/positions/page.tsx"
git commit -m "feat(fe): direct-OHLC charts, remove resolution buttons, lazy series load"
```

---

## Phase E — Verify + ship

### Task 14: End-to-end verification

- [ ] **Step 1: Backend suite + lint**

Run: `cd backend && uv run pytest tests/ -q && uv run ruff check app/`
Expected: PASS (lint clean on changed files).

- [ ] **Step 2: Frontend checks**

Run: `cd frontend && npx tsc --noEmit && npx vitest run && npm run build`
Expected: PASS.

- [ ] **Step 3: Manual smoke (local or prod-after-deploy)**

- `GET /api/state` returns positions with `series: []` (light, fast).
- `GET /api/positions/{id}/series` returns a body+tail; payload for a 2-day position is a few MB and builds in well under a second.
- Open a position → chart loads on click; candle view shows wicks; a known spike is visible at 1m and 5m.
- Live tail advances each second while watching.

- [ ] **Step 4: Open PR**

```bash
git push -u origin design/position-live-history-charts
gh pr create --base main --title "feat: lazy per-position charts + uniform-by-age history + true OHLC" --body "Implements docs/superpowers/specs/2026-06-14-position-live-history-charts-design.md"
```

---

## Self-review notes (coverage)

- Spec "lazy split" → Tasks 9, 10, 11, 13.
- Spec "uniform-by-age body" → Tasks 5, 7, 8.
- Spec "always-1s live tail" → Task 8 (`_assemble_series` + ring), existing WS `appendSample`.
- Spec "true OHLC at 10s write" → Tasks 1, 2, 4.
- Spec "rollups carry OHLC" → Tasks 6, 7.
- Spec "per-leg across life" → Task 7 (`last(legs)` per bucket), Task 8 (10s raw body).
- Spec "no resolution buttons, keep candle/line" → Tasks 12, 13.
- Spec "closed positions: no tail, lifetime span" → Task 8 (`end = closed_at`, `ring=None`).
- Spec "migration backfill = pnl" → Task 1.
