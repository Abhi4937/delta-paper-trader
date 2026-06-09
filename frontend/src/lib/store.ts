"use client";

import { create } from "zustand";
import { connectChain, fetchAtmIv, fetchExpiries, fetchMargin, fetchMarks } from "./api";
import { estimateMargin, legPnl, netPnl } from "./engine";
import { type ExitDecision, type ExitInput, evaluateExit } from "./exit";
import type {
  Contract,
  LedgerEntry,
  Leg,
  LegSample,
  LogEntry,
  MarginBadge,
  OptionChain,
  Position,
  SeriesSample,
  Side,
  Underlying,
} from "./types";

let counter = 0;
const uid = () => `${Date.now()}-${counter++}`;
// Paper account in USD (Delta crypto options settle in USD). 1 lot = 0.001 BTC.
const START_BALANCE = 5000;
// MTM is sampled ~1/sec; retain the full series from position open (12h safety
// ceiling) so 1m/5m timeframe charts can aggregate the whole life of the trade.
const MTM_CAP = 12 * 60 * 60;
export const USDINR = 85; // Delta uses a fixed 1 USD = 85 INR

export type Currency = "USD" | "INR";

// live mark map (symbol -> latest mark/bid/ask/iv + per-option greeks), fed by the
// chain WS + marks poll. Greeks are read-only from Delta; the client aggregates.
interface Mark {
  mark: number;
  bid: number;
  ask: number;
  iv: number;
  delta: number;
  gamma: number;
  theta: number;
  vega: number;
  ts: number; // when this mark was last written (for stale-mark detection)
}
const marks = new Map<string, Mark>();
const STALE_MS = 10_000; // suspend auto-exit if a held leg's mark is older than this
const spots = new Map<string, number>(); // underlying -> latest spot (for notional fee)
const atmIvs = new Map<string, number>(); // `${underlying}|${expiry}` -> ATM mark IV
function markRecord(legs: Leg[]): Record<string, number> {
  const m: Record<string, number> = {};
  for (const l of legs) m[l.symbol] = marks.get(l.symbol)?.mark ?? l.entry;
  return m;
}
// Exit fills cross the spread the other way (close a long at bid, a short at ask)
// → realized PnL on close carries exit slippage; live MTM keeps using the mark.
function exitFillRecord(legs: Leg[]): Record<string, number> {
  const m: Record<string, number> = {};
  for (const l of legs) {
    const q = marks.get(l.symbol);
    m[l.symbol] = l.side === "buy" ? q?.bid || q?.mark || l.entry : q?.ask || q?.mark || l.entry;
  }
  return m;
}

// --- position-analytics sampling -------------------------------------------
// Net greek = Σ (signed_qty × contract_value × per-option greek). Delta → BTC,
// theta → USD/day, vega → USD per 1 vol-point. Standard aggregation; validate
// vs a real Delta account before relying on the absolute numbers.
const legSign = (side: Side): number => (side === "buy" ? 1 : -1);

function netGreeks(legs: Leg[]): { delta: number; theta: number; vega: number } {
  let delta = 0;
  let theta = 0;
  let vega = 0;
  for (const l of legs) {
    const m = marks.get(l.symbol);
    if (!m) continue;
    const k = legSign(l.side) * l.qty * l.contractValue;
    delta += k * m.delta;
    theta += k * m.theta;
    vega += k * m.vega;
  }
  return { delta, theta, vega };
}

function sampleLegs(legs: Leg[]): Record<string, LegSample> {
  const out: Record<string, LegSample> = {};
  for (const l of legs) {
    const m = marks.get(l.symbol);
    out[l.id] = {
      pnl: legPnl(l, m?.mark ?? l.entry),
      iv: m?.iv ?? 0,
      delta: legSign(l.side) * l.qty * l.contractValue * (m?.delta ?? 0),
    };
  }
  return out;
}

// One sampled row (net + per-leg) for a position at time `now`. ATM IV is sampled
// for every distinct expiry the legs span (so calendars get one ATM line each).
function buildSample(p: Position, now: number): SeriesSample {
  const ng = netGreeks(p.legs);
  const atmIv: Record<string, number> = {};
  for (const e of new Set(p.legs.map((l) => l.expiry))) {
    atmIv[e] = atmIvs.get(`${p.underlying}|${e}`) ?? 0;
  }
  return {
    t: now,
    pnl: netPnl(p.legs, markRecord(p.legs)),
    delta: ng.delta,
    theta: ng.theta,
    vega: ng.vega,
    atmIv,
    legs: sampleLegs(p.legs),
  };
}

// --- auto-exit -------------------------------------------------------------
const legMarkAge = (sym: string, now: number): number => {
  const m = marks.get(sym);
  return m ? now - m.ts : Infinity; // no mark yet → treat as stale
};

function buildExitInput(p: Position, now: number): ExitInput {
  return {
    legs: p.legs.map((l) => ({
      id: l.id,
      pnl: legPnl(l, legMark(l)),
      markAgeMs: legMarkAge(l.symbol, now),
      targetPnl: l.targetPnl,
      stopPnl: l.stopPnl,
      autoExit: l.autoExit,
      closeScope: l.closeScope,
      status: l.status,
    })),
    netPnl: netPnl(p.legs, markRecord(p.legs)),
    margin: p.margin,
    combinedStop: { lossAmount: p.stopLossAmount, lossPctOfMargin: p.stopLossPctOfMargin },
    combinedAutoExit: p.autoExit,
    staleMs: STALE_MS,
  };
}

// Sample each open position's series + evaluate auto-exit. Returns the next
// positions (with `autoExitSuspended` set) and the exit actions to apply AFTER
// the state update (closing strategies/legs is done via store actions).
function tickPositions(positions: Position[], now: number): {
  positions: Position[];
  actions: { posId: string; decision: ExitDecision }[];
} {
  const actions: { posId: string; decision: ExitDecision }[] = [];
  const next = positions.map((p) => {
    if (p.status === "closed") return p;
    const decision = evaluateExit(buildExitInput(p, now));
    if (decision.kind === "close-strategy" || decision.kind === "close-legs") {
      actions.push({ posId: p.id, decision });
    }
    const last = p.series[p.series.length - 1];
    const series = now - last.t >= 1000 ? [...p.series, buildSample(p, now)].slice(-MTM_CAP) : p.series;
    return { ...p, series, autoExitSuspended: decision.kind === "suspended" };
  });
  return { positions: next, actions };
}

function applyExitActions(actions: { posId: string; decision: ExitDecision }[]): void {
  const s = useStore.getState();
  for (const { posId, decision } of actions) {
    if (decision.kind === "close-strategy") s.closePosition(posId, decision.reason);
    else if (decision.kind === "close-legs") for (const id of decision.legIds) s.closeLeg(posId, id, decision.reason);
  }
}

let disconnect: (() => void) | null = null;

interface State {
  underlying: Underlying;
  expiry: string | null;
  chain: OptionChain | null;
  expiries: string[];
  conn: "connecting" | "live" | "down";
  tickN: number;
  selected: Leg[];
  positions: Position[];
  balance: number;
  currency: Currency;
  ledger: LedgerEntry[];
  logs: LogEntry[];

  connect: () => void;
  setUnderlying: (u: Underlying) => void;
  setExpiry: (e: string) => void;
  setCurrency: (c: Currency) => void;
  addLeg: (c: Contract, side: Side) => void;
  selectLeg: (c: Contract, side: Side) => void;
  removeLeg: (id: string) => void;
  setLegQty: (id: string, qty: number) => void;
  toggleLegSide: (id: string) => void;
  setLegRisk: (id: string, patch: Partial<Pick<Leg, "targetPnl" | "stopPnl" | "autoExit" | "closeScope">>) => void;
  clearLegs: () => void;
  placeStrategy: (name: string, opts: { target: number | null; stopLossAmount: number | null; stopLossPctOfMargin: number | null; autoExit: boolean; margin?: number; badge?: MarginBadge }) => void;
  closePosition: (id: string, reason?: string) => void;
  closeLeg: (id: string, legId: string, reason?: string) => Promise<void>;
  setPositionStop: (id: string, patch: Partial<Pick<Position, "targetPnl" | "stopLossAmount" | "stopLossPctOfMargin" | "autoExit">>) => void;
  setPositionLegRisk: (id: string, legId: string, patch: Partial<Pick<Leg, "targetPnl" | "stopPnl" | "autoExit" | "closeScope">>) => void;
  addNote: (id: string, kind: "entry" | "exit", body: string) => void;
}

function log(logs: LogEntry[], action: string, detail: string, tone?: LogEntry["tone"]): LogEntry[] {
  return [{ t: Date.now(), action, detail, tone }, ...logs].slice(0, 200);
}

export const useStore = create<State>((set, get) => ({
  underlying: "BTC",
  expiry: null,
  chain: null,
  expiries: [],
  conn: "connecting",
  tickN: 0,
  selected: [],
  positions: [],
  balance: START_BALANCE,
  currency: "USD",
  ledger: [{ t: Date.now(), type: "deposit", amount: START_BALANCE, balanceAfter: START_BALANCE, ref: "seed" }],
  logs: [{ t: Date.now(), action: "session", detail: "Paper account funded $5,000", tone: "info" }],

  connect: () => {
    disconnect?.();
    set({ conn: "connecting" });
    const { underlying, expiry } = get();
    fetchExpiries(underlying).then((e) => set({ expiries: e })).catch(() => {});
    disconnect = connectChain(
      underlying,
      expiry,
      ({ chain, expiries }) => {
        // merge live marks + greeks + spot + ATM IV
        const now = Date.now();
        for (const r of chain.rows) {
          for (const c of [r.call, r.put])
            marks.set(c.symbol, {
              mark: c.mark, bid: c.bid, ask: c.ask, iv: c.iv,
              delta: c.greeks.delta, gamma: c.greeks.gamma, theta: c.greeks.theta, vega: c.greeks.vega, ts: now,
            });
        }
        spots.set(chain.underlying, chain.spot);
        atmIvs.set(`${chain.underlying}|${chain.expiry}`, chain.atmIv);
        const s = get();
        const { positions, actions } = tickPositions(s.positions, now);
        set({
          chain,
          expiries,
          expiry: s.expiry ?? chain.expiry,
          conn: "live",
          tickN: s.tickN + 1,
          positions,
        });
        applyExitActions(actions);
      },
      (st) => set({ conn: st === "live" ? "live" : "down" }),
    );
  },

  setUnderlying: (u) => {
    set({ underlying: u, expiry: null, chain: null });
    get().connect();
  },
  setExpiry: (e) => {
    set({ expiry: e, chain: null });
    get().connect();
  },
  setCurrency: (c) => set({ currency: c }),

  addLeg: (c, side) =>
    set((s) => {
      if (!s.chain) return s;
      const leg: Leg = {
        id: uid(),
        symbol: c.symbol,
        productId: c.productId,
        underlying: s.underlying,
        type: c.type,
        strike: c.strike,
        contractValue: c.contractValue,
        expiry: s.chain.expiry,
        dte: s.chain.dte,
        side,
        qty: 1,
        entry: side === "buy" ? c.ask || c.mark : c.bid || c.mark,
        markAtEntry: c.mark,
        spotAtEntry: s.chain.spot,
        targetPnl: null,
        stopPnl: null,
        autoExit: false,
        closeScope: "leg",
        status: "open",
      };
      return { selected: [...s.selected, leg] };
    }),
  // set-or-add: if this contract is already in the basket, set its side; else add
  selectLeg: (c, side) =>
    set((s) => {
      if (!s.chain) return s;
      const entry = side === "buy" ? c.ask || c.mark : c.bid || c.mark;
      if (s.selected.some((l) => l.symbol === c.symbol)) {
        return {
          selected: s.selected.map((l) => (l.symbol === c.symbol ? { ...l, side, entry } : l)),
        };
      }
      const leg: Leg = {
        id: uid(),
        symbol: c.symbol,
        productId: c.productId,
        underlying: s.underlying,
        type: c.type,
        strike: c.strike,
        contractValue: c.contractValue,
        expiry: s.chain.expiry,
        dte: s.chain.dte,
        side,
        qty: 1,
        entry,
        markAtEntry: c.mark,
        spotAtEntry: s.chain.spot,
        targetPnl: null,
        stopPnl: null,
        autoExit: false,
        closeScope: "leg",
        status: "open",
      };
      return { selected: [...s.selected, leg] };
    }),
  removeLeg: (id) => set((s) => ({ selected: s.selected.filter((l) => l.id !== id) })),
  setLegQty: (id, qty) =>
    set((s) => ({ selected: s.selected.map((l) => (l.id === id ? { ...l, qty: Math.max(1, qty) } : l)) })),
  toggleLegSide: (id) =>
    set((s) => ({
      selected: s.selected.map((l) => {
        if (l.id !== id) return l;
        const side: Side = l.side === "buy" ? "sell" : "buy";
        const m = marks.get(l.symbol);
        const entry = side === "buy" ? m?.ask || l.entry : m?.bid || l.entry;
        return { ...l, side, entry };
      }),
    })),
  setLegRisk: (id, patch) =>
    set((s) => ({ selected: s.selected.map((l) => (l.id === id ? { ...l, ...patch } : l)) })),
  clearLegs: () => set({ selected: [] }),

  placeStrategy: (name, opts) =>
    set((s) => {
      if (s.selected.length === 0 || !s.chain) return s;
      // margin from /api/margin (or fallback) is USD -> INR
      const margin = (opts.margin ?? estimateMargin(s.selected, s.chain.spot));
      const pos: Position = {
        id: uid(),
        name,
        underlying: s.underlying,
        expiry: s.chain.expiry,
        legs: s.selected,
        margin,
        marginBadge: opts.badge ?? "est",
        openedAt: Date.now(),
        status: "open",
        targetPnl: opts.target,
        stopLossAmount: opts.stopLossAmount,
        stopLossPctOfMargin: opts.stopLossPctOfMargin,
        autoExit: opts.autoExit,
        autoExitSuspended: false,
        series: [],
        notes: [],
      };
      pos.series = [buildSample(pos, Date.now())]; // seed the first sample from open
      const balanceAfter = s.balance - margin;
      // keep `selected` (the basket) — the user navigates to Positions instead
      return {
        positions: [pos, ...s.positions],
        balance: balanceAfter,
        ledger: [{ t: Date.now(), type: "margin_reserve", amount: -margin, balanceAfter, ref: pos.name }, ...s.ledger],
        logs: log(s.logs, "PLACE", `${pos.name} · ${pos.legs.length} legs · margin $${fmt(margin)}`, "info"),
      };
    }),

  closePosition: (id, reason) =>
    set((s) => {
      const pos = s.positions.find((p) => p.id === id);
      if (!pos || pos.status === "closed") return s;
      // gross at exit fills (carries exit slippage); net = gross − entry fee − exit fee
      const gross = netPnl(pos.legs, exitFillRecord(pos.legs));
      const fees = entryFee(pos) + exitFee(pos);
      const net = gross - fees;
      const afterRelease = s.balance + pos.margin;
      const afterFees = afterRelease - fees;
      const balanceAfter = afterFees + gross;
      return {
        positions: s.positions.map((p) => (p.id === id ? { ...p, status: "closed" } : p)),
        balance: balanceAfter,
        ledger: [
          { t: Date.now(), type: "realized", amount: gross, balanceAfter, ref: pos.name },
          { t: Date.now(), type: "fee", amount: -fees, balanceAfter: afterFees, ref: pos.name },
          { t: Date.now(), type: "margin_release", amount: pos.margin, balanceAfter: afterRelease, ref: pos.name },
          ...s.ledger,
        ],
        logs: log(s.logs, reason ? `AUTO-EXIT (${reason})` : "CLOSE",
          `${pos.name} · net ${net >= 0 ? "+" : ""}$${fmt(net)} (gross ${gross >= 0 ? "+" : ""}$${fmt(gross)} − fees $${fmt(fees)})`,
          net >= 0 ? "pos" : "neg"),
      };
    }),

  // close ONE leg: realize it, recompute exact margin for the rest, free the
  // difference. Closing the last open leg closes the whole strategy.
  closeLeg: async (id, legId, reason) => {
    const pos = get().positions.find((p) => p.id === id);
    if (!pos || pos.status === "closed") return;
    const leg = pos.legs.find((l) => l.id === legId);
    if (!leg) return;
    const remaining = pos.legs.filter((l) => l.id !== legId);

    const exitFill = exitFillRecord([leg])[leg.symbol];
    const grossLeg = legPnl(leg, exitFill);
    const spotNow = spots.get(leg.underlying) ?? leg.spotAtEntry;
    const feesLeg =
      legFee(leg.entry, leg.spotAtEntry, leg.contractValue, leg.qty) +
      legFee(exitFill, spotNow, leg.contractValue, leg.qty);

    let newMargin = 0;
    let badge: MarginBadge = pos.marginBadge;
    if (remaining.length > 0) {
      const r = await fetchMargin(
        pos.underlying,
        remaining.map((l) => ({ product_id: l.productId, side: l.side, size: l.qty })),
      );
      if (r) {
        newMargin = r.margin;
        badge = r.badge as MarginBadge;
      } else {
        newMargin = pos.margin; // margin fetch failed → keep the reserve unchanged
      }
    }
    const label = `${leg.strike}${leg.type === "call" ? "CE" : "PE"}`;

    set((s) => {
      const p = s.positions.find((x) => x.id === id);
      if (!p || p.status === "closed") return s;
      const closing = remaining.length === 0;
      const released = p.margin - newMargin; // freed margin back to balance
      const balanceAfter = s.balance + grossLeg - feesLeg + released;
      const net = grossLeg - feesLeg;
      return {
        positions: s.positions.map((x) =>
          x.id !== id
            ? x
            : closing
              ? { ...x, status: "closed" }
              : { ...x, legs: remaining, margin: newMargin, marginBadge: badge },
        ),
        balance: balanceAfter,
        ledger: [
          { t: Date.now(), type: "realized", amount: grossLeg, balanceAfter, ref: `${p.name} · ${label}` },
          { t: Date.now(), type: "fee", amount: -feesLeg, balanceAfter, ref: p.name },
          { t: Date.now(), type: "margin_release", amount: released, balanceAfter, ref: p.name },
          ...s.ledger,
        ],
        logs: log(
          s.logs,
          reason ? `LEG EXIT (${reason})` : "CLOSE LEG",
          `${p.name} · ${label} · net ${net >= 0 ? "+" : ""}$${fmt(net)}${closing ? " · strategy closed" : ""}`,
          net >= 0 ? "pos" : "neg",
        ),
      };
    });
  },

  setPositionStop: (id, patch) =>
    set((s) => ({ positions: s.positions.map((p) => (p.id === id ? { ...p, ...patch } : p)) })),
  setPositionLegRisk: (id, legId, patch) =>
    set((s) => ({
      positions: s.positions.map((p) =>
        p.id === id ? { ...p, legs: p.legs.map((l) => (l.id === legId ? { ...l, ...patch } : l)) } : p,
      ),
    })),

  addNote: (id, kind, body) =>
    set((s) => ({
      positions: s.positions.map((p) =>
        p.id === id ? { ...p, notes: [...p.notes, { kind, body, at: Date.now() }] } : p,
      ),
    })),
}));

// Keep held-position marks live for ALL expiries (the chain WS only covers the
// viewed one) — so every isolated strategy MTMs independently.
let posMarkTimer: ReturnType<typeof setInterval> | null = null;
async function pollPositionMarks(): Promise<void> {
  const open = useStore.getState().positions.filter((p) => p.status === "open");
  const syms = [...new Set(open.flatMap((p) => p.legs.map((l) => l.symbol)))];
  if (syms.length === 0) return;
  // pull fresh marks (+greeks) for held legs and ATM IV for every leg-expiry
  const expiryPairs = [
    ...new Map(
      open.flatMap((p) => p.legs.map((l) => [`${p.underlying}|${l.expiry}`, { underlying: p.underlying, expiry: l.expiry }])),
    ).values(),
  ];
  const [data] = await Promise.all([
    fetchMarks(syms),
    fetchAtmIv(expiryPairs).then((iv) => {
      for (const [k, v] of Object.entries(iv)) atmIvs.set(k, v);
    }),
  ]);
  const now = Date.now();
  for (const [sym, m] of Object.entries(data)) marks.set(sym, { ...m, ts: now });
  let actions: { posId: string; decision: ExitDecision }[] = [];
  useStore.setState((s) => {
    const t = tickPositions(s.positions, now);
    actions = t.actions;
    return { tickN: s.tickN + 1, positions: t.positions };
  });
  applyExitActions(actions);
}

export function startStream(): () => void {
  useStore.getState().connect();
  if (!posMarkTimer) posMarkTimer = setInterval(pollPositionMarks, 1500);
  return () => {
    disconnect?.();
    if (posMarkTimer) {
      clearInterval(posMarkTimer);
      posMarkTimer = null;
    }
  };
}

export function legMark(leg: Leg): number {
  return marks.get(leg.symbol)?.mark ?? leg.entry;
}
export function legIv(leg: Leg): number {
  return marks.get(leg.symbol)?.iv ?? 0;
}
export function spotOf(u: Underlying): number {
  return spots.get(u) ?? 0;
}
export function positionPnl(p: Position): number {
  return netPnl(p.legs, markRecord(p.legs));
}
// Cost of crossing the spread on entry (USD). Already reflected in MTM via the
// fill price; shown separately for transparency. Brokerage is NOT included here.
export function entrySlippage(p: Position): number {
  return p.legs.reduce(
    (s, l) => s + Math.abs(l.entry - l.markAtEntry) * l.qty * l.contractValue,
    0,
  );
}

// Delta India options fee = min(rate·notional, cap·premium), then optional
// referral discount, then +18% GST. Per leg, on entry AND exit.
// notional = spot·cv·qty, premium = price·cv·qty.
//
// Delta runs promos that change the options rate, so the OFFER is a first-class,
// explicit choice — "check the offer before the brokerage calculation". Each
// schedule is named; ACTIVE_FEE selects the one in force. Verified to the cent
// against Delta's own fee calculator. Re-check delta.exchange/fees when promos change.
// Sources: delta.exchange/support .../80001177864 · delta.exchange/fees
export interface FeeSchedule {
  name: string;
  notionalRate: number; // fraction of notional
  premiumCap: number; // max fraction of premium
}
export const FEE_SCHEDULES = {
  standard: { name: "Standard", notionalRate: 0.0003, premiumCap: 0.1 }, // 0.03% / 10%
  optionsCarnival: { name: "Options Carnival", notionalRate: 0.0001, premiumCap: 0.035 }, // 0.010% / 3.5%
} as const;
// ACTIVE offer in force right now (verified against Delta's calculator).
// Switch to FEE_SCHEDULES.standard when the Options Carnival promo ends.
export const ACTIVE_FEE: FeeSchedule = FEE_SCHEDULES.optionsCarnival;
const GST = 0.18; // 18% GST on the fee
export const FEE_DISCOUNT = 0; // DELTAEARN referral 10% — account not eligible
function legFee(price: number, spot: number, cv: number, qty: number): number {
  const base = Math.min(
    ACTIVE_FEE.notionalRate * spot * cv * qty,
    ACTIVE_FEE.premiumCap * price * cv * qty,
  );
  return base * (1 - FEE_DISCOUNT) * (1 + GST);
}
export function entryFee(p: Position): number {
  return p.legs.reduce((s, l) => s + legFee(l.entry, l.spotAtEntry, l.contractValue, l.qty), 0);
}
export function exitFee(p: Position): number {
  const fills = exitFillRecord(p.legs);
  return p.legs.reduce((s, l) => {
    const spot = spots.get(l.underlying) ?? l.spotAtEntry;
    return s + legFee(fills[l.symbol], spot, l.contractValue, l.qty);
  }, 0);
}
// USD money formatter (2 decimals).
export function fmt(x: number): string {
  return x.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

// Format a USD value in the chosen currency (Delta-style $/₹ at fixed 85).
export function money(usd: number, currency: Currency): string {
  return currency === "INR"
    ? `₹${(usd * USDINR).toLocaleString("en-IN", { maximumFractionDigits: 0 })}`
    : `$${fmt(usd)}`;
}

// Both, primary first: "$1.14 · ₹97".
export function moneyBoth(usd: number, primary: Currency): string {
  const other: Currency = primary === "USD" ? "INR" : "USD";
  return `${money(usd, primary)} · ${money(usd, other)}`;
}
