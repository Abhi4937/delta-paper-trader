// Pure risk/exit decision logic for paper strategies. No I/O: given current leg
// PnLs, mark ages, and stop settings, decide what (if anything) to auto-exit.
//
// RULES (see CLAUDE.md domain rules):
// - Auto-exit is SUSPENDED whenever any open leg's mark is stale — never exit on
//   bad data. Suspension wins over every stop.
// - Combined net-capital stop closes the whole strategy.
// - Per-leg TP/SL act on the leg's PnL ($); a leg's closeScope decides whether a
//   trigger closes just that leg or the whole strategy.

export interface ExitLeg {
  id: string;
  pnl: number; // current leg PnL (USD)
  markAgeMs: number; // age of this leg's latest mark
  targetPnl: number | null;
  stopPnl: number | null;
  autoExit: boolean;
  closeScope: "leg" | "strategy";
  status: "open" | "closed";
}

export interface CombinedStop {
  lossAmount: number | null; // absolute loss floor (USD, magnitude)
  lossPctOfMargin: number | null; // loss as % of reserved margin (magnitude)
}

export interface ExitInput {
  legs: ExitLeg[];
  netPnl: number; // strategy net PnL (USD)
  margin: number; // reserved margin (USD), for the % stop
  combinedStop: CombinedStop | null;
  combinedAutoExit: boolean;
  staleMs: number; // mark age beyond which auto-exit is suspended
}

export type ExitDecision =
  | { kind: "none" }
  | { kind: "suspended" }
  | { kind: "close-strategy"; reason: string }
  | { kind: "close-legs"; legIds: string[]; reason: string };

// Net-PnL floor implied by the combined stop (null if none set). % is of margin.
export function combinedFloor(stop: CombinedStop | null, margin: number): number | null {
  if (!stop) return null;
  if (stop.lossAmount != null) return -Math.abs(stop.lossAmount);
  if (stop.lossPctOfMargin != null) return -(margin * Math.abs(stop.lossPctOfMargin)) / 100;
  return null;
}

export function evaluateExit(input: ExitInput): ExitDecision {
  const open = input.legs.filter((l) => l.status === "open");
  if (open.length === 0) return { kind: "none" };

  // 1) stale marks → suspend ALL auto-exit (never act on bad data)
  if (open.some((l) => l.markAgeMs > input.staleMs)) return { kind: "suspended" };

  // 2) combined net-capital stop → close the whole strategy
  if (input.combinedAutoExit) {
    const floor = combinedFloor(input.combinedStop, input.margin);
    if (floor != null && input.netPnl <= floor) {
      return { kind: "close-strategy", reason: "combined SL" };
    }
  }

  // 3) per-leg TP/SL (on leg PnL)
  const triggered = open.filter(
    (l) =>
      l.autoExit &&
      ((l.targetPnl != null && l.pnl >= l.targetPnl) ||
        (l.stopPnl != null && l.pnl <= l.stopPnl)),
  );
  if (triggered.length === 0) return { kind: "none" };
  // a leg set to "close whole strategy" escalates the trigger
  if (triggered.some((l) => l.closeScope === "strategy")) {
    return { kind: "close-strategy", reason: "leg TP/SL" };
  }
  return { kind: "close-legs", legIds: triggered.map((l) => l.id), reason: "leg TP/SL" };
}
