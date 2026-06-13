import { describe, expect, it } from "vitest";
import { atmPoints, buckets, capPoints, legPoints, netPoints, toLine } from "./chartData";
import type { SeriesSample } from "./types";

function sample(t: number, over: Partial<SeriesSample> = {}): SeriesSample {
  return { t, pnl: 0, delta: 0, theta: 0, vega: 0, atmIv: {}, legs: {}, ...over };
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

  it("buckets aggregates into OHLC with open = previous close", () => {
    const b = buckets([{ t: 1000, v: 10 }, { t: 1500, v: 12 }, { t: 2000, v: 8 }], 1);
    expect(b.length).toBe(2);
    expect(b[0]).toMatchObject({ open: 10, high: 12, low: 10, close: 12 });
    expect(b[1]).toMatchObject({ open: 12, high: 12, low: 8, close: 8 });
  });

  it("toLine returns the close of each bucket", () => {
    expect(toLine([{ t: 1000, v: 10 }, { t: 2000, v: 8 }], 1).map((p) => p.value)).toEqual([10, 8]);
  });
});
