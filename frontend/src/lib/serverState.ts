// Pure helpers for applying a server state snapshot to the client store.
// Kept dependency-free (types only) so it's unit-testable without the zustand /
// localStorage-bound store module.
import type { Position } from "./types";

// GET /api/state ships every position with an EMPTY series — a position's MTM/IV/greek
// history is loaded lazily per position via GET /api/positions/{id}/series (the charts
// overhaul moved it off the light state snapshot). So when applying a fresh server
// snapshot, KEEP any series we've already loaded for a position instead of clobbering it
// with the incoming []. Without this, the periodic re-hydrate (and every mutation refetch)
// wiped the loaded entry→now history, leaving only the handful of WS-tick samples since
// the wipe — the "only latest data, old history gone" regression.
export function mergePositionsPreservingSeries(
  prev: Position[],
  incoming: Position[],
): Position[] {
  const prevSeries = new Map(prev.map((p) => [p.id, p.series]));
  return incoming.map((p) => {
    const kept = prevSeries.get(p.id);
    // only fall back to the kept series when the snapshot brought none (it never does
    // for /api/state); if a snapshot ever carries series, that wins.
    return p.series.length === 0 && kept && kept.length > 0 ? { ...p, series: kept } : p;
  });
}
