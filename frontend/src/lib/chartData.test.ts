import { describe, expect, it } from "vitest";
import { atmPoints, buckets, legPoints, netPoints, toLine } from "./chartData";
import type { SeriesSample } from "./types";

function sample(t: number, over: Partial<SeriesSample> = {}): SeriesSample {
  return { t, pnl: 0, delta: 0, theta: 0, vega: 0, atmIv: {}, legs: {}, ...over };
}

describe("chartData", () => {
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
