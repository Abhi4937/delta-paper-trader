// Pure data-prep for the position charts — no React / no lightweight-charts runtime, so
// it's unit-testable. The chart regression (leg/IV lines losing their points) lived in
// exactly this layer, so these functions are covered by chartData.test.ts.
import type { Time, UTCTimestamp } from "lightweight-charts";
import type { SeriesSample } from "./types";

export type TF = 1 | 60 | 300;
export type Pt = { t: number; v: number };
export type OHLC = { time: UTCTimestamp; open: number; high: number; low: number; close: number };

// Aggregate {t(ms), v} samples into `tf`-second OHLC buckets. Open = previous bucket's
// close (continuous / gap-free) so even 1s candles show direction.
export function buckets(pts: Pt[], tf: TF): OHLC[] {
  const map = new Map<number, OHLC>();
  const order: number[] = [];
  let prevClose: number | null = null;
  for (const p of pts) {
    const bk = Math.floor(p.t / 1000 / tf) * tf;
    let b = map.get(bk);
    if (!b) {
      const o: number = prevClose ?? p.v;
      b = { time: bk as UTCTimestamp, open: o, high: Math.max(o, p.v), low: Math.min(o, p.v), close: p.v };
      map.set(bk, b);
      order.push(bk);
    } else {
      b.high = Math.max(b.high, p.v);
      b.low = Math.min(b.low, p.v);
      b.close = p.v;
    }
    prevClose = b.close;
  }
  return order.map((bk) => map.get(bk)!);
}

export const toLine = (pts: Pt[], tf: TF): { time: Time; value: number }[] =>
  buckets(pts, tf).map((b) => ({ time: b.time, value: b.close }));

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
