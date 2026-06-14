import { describe, expect, it } from "vitest";
import { atmPoints, capPoints, dedupBySecond, legPoints, netMtmCandles, netPoints } from "./chartData";
import type { SeriesSample } from "./types";

function sample(t: number, over: Partial<SeriesSample> = {}): SeriesSample {
  return { t, pnl: 0, pnlOpen: 0, pnlHigh: 0, pnlLow: 0, delta: 0, theta: 0, vega: 0, atmIv: {}, legs: {}, ...over };
}

describe("chartData", () => {
  it("capPoints is a no-op at/under the cap", () => {
    const arr = Array.from({ length: 100 }, (_, i) => ({ time: i, value: i }));
    expect(capPoints(arr, 1500)).toBe(arr);
    expect(capPoints(arr, 100)).toBe(arr);
  });

  it("capPoints downsamples to ~max, preserving first+last and ascending order", () => {
    const arr = Array.from({ length: 10000 }, (_, i) => ({ time: i, value: i }));
    const out = capPoints(arr, 1500);
    expect(out.length).toBeLessThanOrEqual(1501);
    expect(out.length).toBeGreaterThan(1400);
    expect(out[0]).toEqual({ time: 0, value: 0 }); // entry preserved
    expect(out[out.length - 1]).toEqual({ time: 9999, value: 9999 }); // latest preserved
    const times = out.map((p) => p.time);
    expect(times).toEqual([...times].sort((a, b) => a - b)); // strictly ascending kept
    expect(new Set(times).size).toBe(times.length); // no dup times (lightweight-charts requires)
  });

  it("capPoints keeps the sparse early region (time-aware, not index striding)", () => {
    // THE 1C bug: a mixed-resolution series (sparse 10s-old + dense 1s-recent). Index
    // striding starved the old region (~a handful of points); time-bucketing keeps it.
    const old10s = Array.from({ length: 600 }, (_, i) => ({ time: i * 10, value: i })); // 0..5990s @10s
    const recent1s = Array.from({ length: 6000 }, (_, i) => ({ time: 6000 + i, value: i })); // 6000..11999s @1s
    const arr = [...old10s, ...recent1s]; // 6600 pts, over the cap
    const out = capPoints(arr, 1500);
    expect(out.length).toBeLessThanOrEqual(1501);
    const oldKept = out.filter((p) => p.time < 6000).length; // old half of the TIME span
    expect(oldKept).toBeGreaterThan(400); // time-aware keeps it; index striding gave ~136
    expect(out[0].time).toBe(0); // entry preserved
    expect(out[out.length - 1].time).toBe(11999); // latest preserved
  });

  it("netPoints maps the chosen metric per sample", () => {
    const s = [sample(1000, { pnl: 5, delta: 0.3 }), sample(2000, { pnl: -2, delta: -0.1 })];
    expect(netPoints(s, "pnl")).toEqual([{ t: 1000, v: 5 }, { t: 2000, v: -2 }]);
    expect(netPoints(s, "delta")).toEqual([{ t: 1000, v: 0.3 }, { t: 2000, v: -0.1 }]);
  });

  // The regression guard: with leg data present, every sample yields a leg point.
  // (The lightweight-ring bug emptied s.legs → these collapsed to flat lines.)
  it("legPoints returns one point per sample carrying the leg's value", () => {
    const s = [
      sample(1000, { legs: { L1: { pnl: 3, iv: 0.5, delta: 0.2, theta: -1, vega: 2 } } }),
      sample(2000, { legs: { L1: { pnl: 7, iv: 0.6, delta: 0.25, theta: -1, vega: 2 } } }),
    ];
    expect(legPoints(s, "L1", (x) => x?.pnl ?? 0)).toEqual([{ t: 1000, v: 3 }, { t: 2000, v: 7 }]);
    expect(legPoints(s, "L1", (x) => (x?.iv ?? 0) * 100)).toEqual([{ t: 1000, v: 50 }, { t: 2000, v: 60 }]);
  });

  it("legPoints yields 0 (no crash) for a sample missing that leg", () => {
    expect(legPoints([sample(3000)], "L1", (x) => x?.pnl ?? 0)).toEqual([{ t: 3000, v: 0 }]);
  });

  it("atmPoints returns IV percent per sample, 0 when the expiry is absent", () => {
    const s = [sample(1000, { atmIv: { "2026-06-13": 0.45 } }), sample(2000)];
    expect(atmPoints(s, "2026-06-13")).toEqual([{ t: 1000, v: 45 }, { t: 2000, v: 0 }]);
  });

  it("netMtmCandles renders server OHLC directly (one candle per sample)", () => {
    const series = [
      sample(60000, { pnl: 12, pnlOpen: 10, pnlHigh: 25, pnlLow: -5 }),
      sample(120000, { pnl: 14, pnlOpen: 12, pnlHigh: 14, pnlLow: 11 }),
    ];
    expect(netMtmCandles(series)).toEqual([
      { time: 60, open: 10, high: 25, low: -5, close: 12 },
      { time: 120, open: 12, high: 14, low: 11, close: 14 },
    ]);
  });

  // Two samples that floor to the SAME second must collapse into one candle —
  // lightweight-charts' setData throws on duplicate `time`. Merge OHLC: first open,
  // max high, min low, last close; never emit a duplicate `time`.
  it("netMtmCandles collapses same-second samples into one merged candle (no dup time)", () => {
    const series = [
      sample(60_100, { pnl: 12, pnlOpen: 10, pnlHigh: 25, pnlLow: -5 }), // t=60s
      sample(60_950, { pnl: 14, pnlOpen: 13, pnlHigh: 30, pnlLow: -9 }), // also t=60s (live throttle ~950ms)
      sample(120_000, { pnl: 20, pnlOpen: 18, pnlHigh: 22, pnlLow: 15 }), // t=120s
    ];
    const out = netMtmCandles(series);
    expect(out).toEqual([
      { time: 60, open: 10, high: 30, low: -9, close: 14 }, // open=first, high=max, low=min, close=last
      { time: 120, open: 18, high: 22, low: 15, close: 20 },
    ]);
    const times = out.map((c) => c.time);
    expect(new Set(times).size).toBe(times.length); // no duplicate time
  });

  it("dedupBySecond collapses consecutive equal-second points to the last value, strictly ascending unique times", () => {
    const out = dedupBySecond([
      { time: 60, value: 1 },
      { time: 60, value: 2 }, // same second → keep this (last)
      { time: 60, value: 3 }, // same second → keep this (last)
      { time: 61, value: 4 },
      { time: 62, value: 5 },
      { time: 62, value: 6 }, // same second → keep this (last)
    ]);
    expect(out).toEqual([
      { time: 60, value: 3 },
      { time: 61, value: 4 },
      { time: 62, value: 6 },
    ]);
    const times = out.map((p) => p.time);
    expect(times).toEqual([...times].sort((a, b) => a - b)); // ascending
    expect(new Set(times).size).toBe(times.length); // unique
  });

  it("dedupBySecond is a no-op when all times are already unique", () => {
    const arr = [
      { time: 10, value: 1 },
      { time: 11, value: 2 },
      { time: 12, value: 3 },
    ];
    expect(dedupBySecond(arr)).toEqual(arr);
  });
});
