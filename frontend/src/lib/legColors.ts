// Per-leg chart colors, shared by the analytics charts and the positions table so
// a leg's table swatch matches its line color. Legs are grouped by EXPIRY HUE; within
// a family, calls are lighter and puts deeper. Hues avoid pure red/green (PnL signs).

const LEG_HUES = [210, 275, 38, 320, 190, 95];

export function expiryHue(expiry: string, expiries: string[]): number {
  const e = Math.max(0, expiries.indexOf(expiry));
  return LEG_HUES[e % LEG_HUES.length];
}

export function legColorFor(expiry: string, type: string, expiries: string[], twin: number): string {
  const hue = (expiryHue(expiry, expiries) + twin * 24) % 360;
  // saturation 25%; call vs put differ by a wide lightness gap (call lighter, put deeper)
  return type === "call" ? `hsl(${hue} 25% 58%)` : `hsl(${hue} 25% 34%)`;
}

// bold ATM-IV line per expiry: bright, tinted by the expiry's hue
export function atmColorFor(expiry: string, expiries: string[]): string {
  return `hsl(${expiryHue(expiry, expiries)} 52% 80%)`;
}

// map leg.id → color for a set of legs (grouping/twins resolved across the set)
export function legColorMap(legs: { id: string; expiry: string; type: string }[]): Record<string, string> {
  const expiries = [...new Set(legs.map((l) => l.expiry))];
  const twinSeen: Record<string, number> = {};
  const out: Record<string, string> = {};
  for (const l of legs) {
    const k = `${l.expiry}|${l.type}`;
    const twin = (twinSeen[k] = (twinSeen[k] ?? -1) + 1);
    out[l.id] = legColorFor(l.expiry, l.type, expiries, twin);
  }
  return out;
}
