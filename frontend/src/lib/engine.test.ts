import { describe, expect, it } from "vitest";
import { groupPnlAtTrigger, legPnlAtTrigger } from "./engine";
import type { Leg } from "./types";

const leg = (id: string, side: "buy" | "sell", entry: number, qty = 1): Leg =>
  ({ id, side, entry, qty, contractValue: 0.001, status: "open" }) as Leg;

describe("P&L at a live SL / target trigger", () => {
  it("sold leg loses as the premium rises, gains as it falls", () => {
    expect(legPnlAtTrigger(leg("p", "sell", 111), 180)).toBeCloseTo(-0.069);
    expect(legPnlAtTrigger(leg("c", "sell", 200), 100)).toBeCloseTo(0.1);
  });
  it("bought leg loses as the premium falls", () => {
    expect(legPnlAtTrigger(leg("b", "buy", 111), 60)).toBeCloseTo(-0.051);
  });
  it("scales with lots and subtracts the exit fee", () => {
    expect(legPnlAtTrigger(leg("p", "sell", 111, 10), 180, 0.02)).toBeCloseTo(-0.71);
  });
  it("group = this leg at its trigger + the others at their current marks, net of fees", () => {
    const legs = [leg("p", "sell", 111), leg("c", "sell", 200), { ...leg("x", "sell", 50), status: "closed" } as Leg];
    const marks: Record<string, number> = { p: 142, c: 150, x: 999 };
    const g = groupPnlAtTrigger(legs, "p", 180, (l) => marks[l.id], () => 0.001);
    // put at 180: -0.069 ; call at its mark 150: +0.05 ; closed leg ignored ; 2 x 0.001 fees
    expect(g).toBeCloseTo(-0.069 + 0.05 - 0.002);
  });
});
