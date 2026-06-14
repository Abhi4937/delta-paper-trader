# Position Live + History Charts — Design Spec

**Date:** 2026-06-14
**Status:** Approved (brainstorm), pending implementation plan
**Area:** position detail charts (MTM / IV / Delta / Theta / Vega), `GET /api/state`, sim tick loop

## Problem

Opening the positions view is slow. `GET /api/state` eagerly builds the **full per-second MTM/Greeks history for every position** (open *and* closed) in one response. With ~10 positions each carrying 8,000–10,500 rows and 3.5–8.4 MB of per-leg JSON, the response is ~50 MB and takes ~20 s to assemble on the 1-vCPU VM.

Two root causes:
1. **No live/history split.** The lightweight table (current PnL/Greeks/margin per position) is bundled with the heavy full-life chart series for *all* positions, eagerly.
2. **No resolution policy.** A 2-day-old position is returned at full 10s raw resolution — old enough to have thousands of rows, but nothing is downsampled.

Secondary correctness issue discovered during design: **peaks vanish on downsample.** History rows store one *instantaneous* value per 10s, so a spike between marks is never captured, and any coarsening keeps only the last value per bucket — so the high/low the position actually hit is lost.

## Goals

- Positions table loads instantly (live snapshot only, no history).
- A position's full per-leg history loads only when you click into it (lazy).
- History payload stays small and bounded regardless of how long a position has been open.
- The chart shows the **true high/low** the position hit, at every resolution (peaks never silently dropped).
- Full per-leg detail (each leg's IV/Delta/Theta/Vega/MTM) across the whole life of the position.

## Non-goals (explicitly deferred)

- **Zoom-to-load** (fetch finer data for an arbitrary zoomed range on demand). Future enhancement; not in this spec.
- **Write-time OHLC for Greeks / per-leg.** Only **net MTM** gets true OHLC now. Greeks stay single-value (line charts; peaks matter far less).
- **Candlestick-pattern detection.** Not meaningful on a PnL curve; out of scope.
- **Manual resolution buttons.** Removed (see Resolution policy). Resolution is automatic by age.

## Core model: uniform-by-age history body + always-live 1s tail

A position's chart has two parts:

1. **History body** (entry → ~15 min ago): **one uniform resolution**, chosen by the position's age. Not a mixed/laddered chart — the whole body is a single resolution.

   | Position age | History-body resolution |
   |---|---|
   | ≤ 15 min | 1s |
   | 15 min – 12 h | 10s |
   | 12 h – 24 h | 1 min |
   | > 24 h | 5 min |

2. **Live tail** (last ~15 min → now): **always 1s, real-time**, regardless of position age. Sourced from the in-memory 15-min ring (`RING_MAXLEN = 900`) plus the WebSocket stream that appends a fresh 1s point each second while the user watches.

So a 2-day position renders as a uniform 5-min body with a 1s live tail at the right edge. Zoomed out, the 15-min tail is sub-pixel; zoomed into the recent window, the 1s detail is there. The body/tail seam at ~15 min ago is the only resolution change, and it is intentional ("history" vs "now").

### Peak fidelity (true OHLC)

- The 1s tick loop already sees every 1-second value. At each 10s DB write, it stores **OHLC of net MTM over that 10s window**: `open` = first, `high` = max, `low` = min, `close` = last. This captures spikes permanently at 1s precision.
- All rollups (1 min, 5 min) aggregate OHLC the standard way: `high` = max of highs, `low` = min of lows, `open` = first open, `close` = last close. Peaks propagate to every resolution.
- The existing **candle view** renders these as wicks → the real high/low is visible at any resolution. No min/max "envelope" overlay is added (the candle *is* the envelope). The line view uses `close`.

## Architecture / data flow

```
Positions table (list)         GET /api/state   → account + positions (current pnl/greeks/
                                                    margin/legs) + ledger + logs. NO series.
Click a position (detail)      GET /api/positions/{id}/series → uniform-by-age body + 1s tail,
                                                    per-leg, net-MTM OHLC. Lazy, one position.
While watching (live)          WS /api/ws/state → 1s sample appended to the open position's
                                                    series (degenerate OHLC: o=h=l=c). Real-time.
```

## Data model changes

`strategy_series` (TimescaleDB hypertable) — add net-MTM OHLC columns:

- `pnl` — **keep** as the close (back-compatible).
- `pnl_open`, `pnl_high`, `pnl_low` — new `double precision`, nullable.

**Migration (Alembic):** add the three columns. Backfill existing rows with `pnl_open = pnl_high = pnl_low = pnl` (no historical sub-10s data exists, so old candles are flat — acceptable; only data written after deploy has true wicks). Do **not** touch the TimescaleDB auto index (`strategy_series_time_idx`) — autogen will try to drop it; strip that line as in prior migrations.

All other fields (`delta`, `theta`, `vega`, `atm_iv`, `legs`) are unchanged and remain single-value (last-in-bucket on rollup).

## Backend changes

### 1. Tick loop (`ticker.py`) — capture net-MTM OHLC at write time

- Maintain a per-position accumulator over each 10s DB-write window: running `first` / `max` / `min` / `last` of net MTM (from the 1s samples already computed each tick).
- On the 10s write, populate `pnl_open/high/low` (and `pnl` as close), then reset the accumulator.
- Memory cost: a few floats per open position. No row-count change. Row size grows by 3 floats.

### 2. Series builder (`service.py`) — uniform-by-age + 1s tail

Replace `_db_series` + `bounded_series` (eager, full-raw) with a per-position builder:

1. Compute `age = now - opened_at`; pick body resolution `R` from the age table.
2. **Body** (entry → 15 min ago):
   - `R = 1s`: whole position is ≤15 min old → entirely from the ring (no DB body).
   - `R = 10s`: raw `strategy_series` rows (already 10s).
   - `R = 1m` / `5m`: TimescaleDB rollup via `time_bucket`, per bucket: `first(pnl_open)`, `max(pnl_high)`, `min(pnl_low)`, `last(pnl)`, `last(delta/theta/vega/atm_iv)`, and the **last row's `legs`** in the bucket (preserves per-leg).
3. **Tail** (last 15 min): the 1s ring (`deque`), as degenerate OHLC points (`o=h=l=c=pnl`).
4. Concatenate body + tail; body covers `< first_tail_t`.

**Open vs closed positions:**
- **Open** — `age = now − opened_at`; body + live 1s tail (ring + WS) as above.
- **Closed** — the ring was freed at close, so there is **no 1s tail**. Use the position's *lifetime* (`closed_at − opened_at`) to pick `R`, and serve the body only, uniform at `R` from entry to `closed_at` (the final ~15 min is at the body resolution, not 1s — the 1s ring no longer exists). No WS updates.

Each point carries: `t`, net MTM `{o,h,l,c}`, `delta`, `theta`, `vega`, `atmIv`, and `legs` (per-leg `{pnl, iv, delta, theta, vega}`).

### 3. API (`api/sim.py`)

- `GET /api/state` — **drop series** from each position. Returns account, positions (current `pnl/delta/theta/vega/margin/legs/...`), ledger, logs. This is the table snapshot. Closed positions included but **without series**.
- `GET /api/positions/{id}/series` — **new**, user-scoped. Returns the uniform-by-age body + 1s tail for one position. 404 if not owned.
- WS `/api/ws/state` — unchanged contract; continues to push 1s samples for open positions (the live tail).

## Frontend changes

- **`lib/api.ts`**: `fetchState` no longer expects `series` on positions; add `fetchPositionSeries(id)` → `GET /api/positions/{id}/series`.
- **`lib/store.ts`**: hydrate positions from `/api/state` without series; series for a position loaded lazily into the store on detail open (cache per position id for the session; live WS ticks append to it).
- **Positions list (`positions/page.tsx`)**: renders from the light snapshot. Clicking a position opens detail and triggers `fetchPositionSeries`; show a loading state until it arrives.
- **`components/PositionCharts.tsx`**:
  - **Remove** the `1s / 1m / 5m` resolution `Seg` and the `tf` state. Resolution is automatic by age.
  - **Keep** the Line / Candle style toggle (MTM).
  - Render server-provided OHLC points directly: candle view uses `{o,h,l,c}` per point; line view uses `c`. Per-leg + Greek panels render `last`-value lines.
  - Live WS samples append as degenerate-OHLC points at the right edge (1s tail growth).
- **`lib/chartData.ts`**: the server now pre-buckets, so client-side `buckets(pts, tf)` re-bucketing is no longer driven by a `tf` toggle. Keep a safety `capPoints` (~1500) for display. Adjust types so points carry net-MTM OHLC.

## Testing

- **Unit (backend):**
  - Age → body-resolution mapping (boundary cases at 15 min / 12 h / 24 h).
  - OHLC accumulator: a synthetic 10s window of 1s ticks with a mid-window spike yields correct `open/high/low/close`.
  - Rollup aggregation: 1 min / 5 min preserve `max(high)` / `min(low)` across child buckets (spike survives coarsening).
  - Body+tail concatenation: tail is 1s, body ends before tail start, per-leg present at every tier.
- **Unit (frontend):** chartData renders OHLC points to candles correctly; degenerate-OHLC (tail/live) renders as a flat candle / continuous line.
- **Integration:** `GET /api/state` returns no series; `GET /api/positions/{id}/series` returns a bounded payload; payload size for a 2-day position is < a few MB and builds in well under a second.
- **Manual / prod:** before/after payload size and load time for `/api/state` and a 2-day position's series; candle wick shows a known spike at 1m and 5m.

## Rollout

1. Migration (add OHLC columns, backfill = `pnl`).
2. Backend: tick-loop OHLC capture + new series builder + API split.
3. Frontend: lazy series fetch + chart changes (remove resolution buttons).
4. Deploy via the standard archive-push; verify payload/latency on prod.

Old positions written before deploy have flat candles (no historical sub-10s data) — expected; new data carries true wicks.

## Open items / future

- **Zoom-to-load** for drilling into an arbitrary old window at 10s — the recoverable path if uniform-by-age ever feels too coarse on old history.
- Optional per-leg / Greek OHLC if peak fidelity is ever wanted beyond net MTM.
- Optional "coarsen-the-view" control if a manual overview toggle is missed.
