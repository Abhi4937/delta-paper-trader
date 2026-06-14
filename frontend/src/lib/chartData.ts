// Pure data-prep for the position charts — no React / no lightweight-charts runtime, so
// it's unit-testable. The chart regression (leg/IV lines losing their points) lived in
// exactly this layer, so these functions are covered by chartData.test.ts.
import type { UTCTimestamp } from "lightweight-charts";
import type { SeriesSample } from "./types";

export type Pt = { t: number; v: number };
export type OHLC = { time: UTCTimestamp; open: number; high: number; low: number; close: number };

// Cap a (time-ascending) series to ~`max` points for display, by EVEN TIME COVERAGE
// (not index striding). These charts are fixed-view (no zoom; the whole trade in a few
// hundred px), so ~max points is visually identical and keeps per-tick setData cheap.
// Time-bucketing (vs the old index stride) is what fixes the "1s view drops the first
// hour" bug: a mixed-resolution series (sparse 10s/1m old + dense 1s recent) kept its
// early region represented instead of being starved. Buckets the span into `max` time
// slots, keeps the first point per slot + the last point. Order-preserving (times stay
// strictly ascending); no duplicate times (lightweight-charts requires that).
export function capPoints<T>(arr: T[], max = 1500): T[] {
  if (arr.length <= max) return arr;
  const timeOf = (p: T): number => (p as unknown as { time: number }).time;
  const t0 = timeOf(arr[0]);
  const slot = (timeOf(arr[arr.length - 1]) - t0 || 1) / max;
  const out: T[] = [];
  let lastSlot = -1;
  for (const p of arr) {
    const s = Math.floor((timeOf(p) - t0) / slot);
    if (s !== lastSlot) {
      out.push(p);
      lastSlot = s;
    }
  }
  if (out[out.length - 1] !== arr[arr.length - 1]) out.push(arr[arr.length - 1]);
  return out;
}

// Net MTM candles straight from the server's per-point OHLC (no client re-bucketing —
// the server already chose the resolution). Time in epoch-seconds for lightweight-charts,
// which REQUIRES strictly-ascending, non-duplicate `time`. Two samples can floor to the
// same second (body/tail seam, or the ~950ms live throttle), so collapse consecutive
// same-second samples into ONE candle: open=first, high=max, low=min, close=last.
// (Input is time-ascending, so equal seconds are always consecutive.)
export function netMtmCandles(series: SeriesSample[]): OHLC[] {
  const out: OHLC[] = [];
  for (const s of series) {
    const time = Math.floor(s.t / 1000) as UTCTimestamp;
    const last = out[out.length - 1];
    if (last && last.time === time) {
      last.high = Math.max(last.high, s.pnlHigh);
      last.low = Math.min(last.low, s.pnlLow);
      last.close = s.pnl; // last sample's close wins
    } else {
      out.push({ time, open: s.pnlOpen, high: s.pnlHigh, low: s.pnlLow, close: s.pnl });
    }
  }
  return out;
}

// Collapse consecutive equal-`time` line points to the LAST value, yielding strictly-
// ascending unique times. lightweight-charts' setData throws on duplicate `time`; same-
// second collisions happen at the body/tail seam and from the ~950ms live-append throttle.
export function dedupBySecond<T extends { time: number; value: number }>(pts: T[]): T[] {
  const out: T[] = [];
  for (const p of pts) {
    const last = out[out.length - 1];
    if (last && last.time === p.time) out[out.length - 1] = p; // keep the last value for this second
    else out.push(p);
  }
  return out;
}

// net metric line (one point per sample)
export function netPoints(series: SeriesSample[], key: "pnl" | "delta" | "theta" | "vega"): Pt[] {
  return series.map((s) => ({ t: s.t, v: s[key] }));
}

// per-leg line — one point per sample (0 when a sample lacks that leg). The lightweight-ring
// bug emptied s.legs, collapsing these to flat lines; this is what tests guard.
export function legPoints(
  series: SeriesSample[],
  legId: string,
  pick: (ls: SeriesSample["legs"][string] | undefined) => number,
): Pt[] {
  return series.map((s) => ({ t: s.t, v: pick(s.legs[legId]) }));
}

// ATM-IV line for an expiry (percent)
export function atmPoints(series: SeriesSample[], expiry: string): Pt[] {
  return series.map((s) => ({ t: s.t, v: (s.atmIv[expiry] ?? 0) * 100 }));
}
