import { describe, expect, it } from "vitest";
import { mergePositionsPreservingSeries } from "./serverState";
import type { Position, SeriesSample } from "./types";

// minimal Position factory — the merge only reads `id` and `series`
const sample = (t: number): SeriesSample =>
  ({ t, pnl: 0, pnlOpen: 0, pnlHigh: 0, pnlLow: 0, delta: 0, theta: 0, vega: 0, atmIv: {}, legs: {} }) as SeriesSample;
const pos = (id: string, series: SeriesSample[]): Position =>
  ({ id, series }) as unknown as Position;

describe("mergePositionsPreservingSeries", () => {
  it("keeps an already-loaded series when the snapshot ships an empty one (the bug)", () => {
    const loaded = [sample(1), sample(2), sample(3)];
    const prev = [pos("a", loaded)];
    const incoming = [pos("a", [])]; // GET /api/state always sends []
    const out = mergePositionsPreservingSeries(prev, incoming);
    expect(out[0].series).toBe(loaded); // preserved, not wiped
  });

  it("leaves a new position's empty series alone (loaded lazily on expand)", () => {
    const out = mergePositionsPreservingSeries([], [pos("new", [])]);
    expect(out[0].series).toEqual([]);
  });

  it("does not resurrect series for a position that has none loaded yet", () => {
    const prev = [pos("a", [])];
    const incoming = [pos("a", [])];
    const out = mergePositionsPreservingSeries(prev, incoming);
    expect(out[0].series).toEqual([]);
  });

  it("prefers a non-empty incoming series if a snapshot ever carries one", () => {
    const fresh = [sample(10)];
    const out = mergePositionsPreservingSeries([pos("a", [sample(1)])], [pos("a", fresh)]);
    expect(out[0].series).toBe(fresh);
  });
});
