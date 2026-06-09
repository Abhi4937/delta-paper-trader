import { describe, expect, it } from "vitest";
import { type ExitLeg, combinedFloor, evaluateExit } from "./exit";

function leg(over: Partial<ExitLeg> = {}): ExitLeg {
  return {
    id: "l1",
    pnl: 0,
    markAgeMs: 0,
    targetPnl: null,
    stopPnl: null,
    autoExit: true,
    closeScope: "leg",
    status: "open",
    ...over,
  };
}

const base = {
  netPnl: 0,
  margin: 100,
  combinedStop: null,
  combinedAutoExit: false,
  staleMs: 10_000,
};

describe("combinedFloor", () => {
  it("absolute loss → negative floor", () => {
    expect(combinedFloor({ lossAmount: 50, lossPctOfMargin: null }, 200)).toBe(-50);
  });
  it("% of margin → negative floor", () => {
    expect(combinedFloor({ lossAmount: null, lossPctOfMargin: 50 }, 200)).toBe(-100);
  });
  it("nothing set → null", () => {
    expect(combinedFloor({ lossAmount: null, lossPctOfMargin: null }, 200)).toBeNull();
    expect(combinedFloor(null, 200)).toBeNull();
  });
});

describe("evaluateExit", () => {
  it("does nothing when no stops are hit", () => {
    expect(evaluateExit({ ...base, legs: [leg({ pnl: 5, targetPnl: 100, stopPnl: -100 })] }))
      .toEqual({ kind: "none" });
  });

  it("no open legs → none", () => {
    expect(evaluateExit({ ...base, legs: [leg({ status: "closed", stopPnl: 0, pnl: -10 })] }))
      .toEqual({ kind: "none" });
  });

  it("stale mark suspends auto-exit even when a stop would fire", () => {
    const legs = [leg({ pnl: -100, stopPnl: -10, markAgeMs: 15_000 })];
    expect(evaluateExit({ ...base, legs })).toEqual({ kind: "suspended" });
  });

  it("combined absolute SL closes the whole strategy", () => {
    expect(
      evaluateExit({
        ...base,
        legs: [leg()],
        netPnl: -60,
        combinedAutoExit: true,
        combinedStop: { lossAmount: 50, lossPctOfMargin: null },
      }),
    ).toEqual({ kind: "close-strategy", reason: "combined SL" });
  });

  it("combined % -of-margin SL triggers at the right level", () => {
    // 50% of margin 100 = floor -50
    const inp = {
      ...base,
      legs: [leg()],
      margin: 100,
      combinedAutoExit: true,
      combinedStop: { lossAmount: null, lossPctOfMargin: 50 },
    };
    expect(evaluateExit({ ...inp, netPnl: -49 })).toEqual({ kind: "none" });
    expect(evaluateExit({ ...inp, netPnl: -50 })).toEqual({ kind: "close-strategy", reason: "combined SL" });
  });

  it("per-leg target closes only that leg (close-legs)", () => {
    const legs = [
      leg({ id: "a", pnl: 20, targetPnl: 15 }),
      leg({ id: "b", pnl: 1, targetPnl: 15 }),
    ];
    expect(evaluateExit({ ...base, legs })).toEqual({ kind: "close-legs", legIds: ["a"], reason: "leg TP/SL" });
  });

  it("per-leg stop with closeScope=strategy escalates to whole strategy", () => {
    const legs = [leg({ id: "a", pnl: -20, stopPnl: -10, closeScope: "strategy" })];
    expect(evaluateExit({ ...base, legs })).toEqual({ kind: "close-strategy", reason: "leg TP/SL" });
  });

  it("ignores legs with autoExit off", () => {
    const legs = [leg({ pnl: -50, stopPnl: -10, autoExit: false })];
    expect(evaluateExit({ ...base, legs })).toEqual({ kind: "none" });
  });
});
