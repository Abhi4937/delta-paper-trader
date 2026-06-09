"use client";

import { create } from "zustand";
import { connectChain, fetchExpiries, fetchMarks } from "./api";
import { estimateMargin, netPnl } from "./engine";
import type {
  Contract,
  LedgerEntry,
  Leg,
  LogEntry,
  OptionChain,
  Position,
  Side,
  Underlying,
} from "./types";

let counter = 0;
const uid = () => `${Date.now()}-${counter++}`;
// Paper account in USD (Delta crypto options settle in USD). 1 lot = 0.001 BTC.
const START_BALANCE = 5000;
export const USDINR = 85; // Delta uses a fixed 1 USD = 85 INR

export type Currency = "USD" | "INR";

// live mark map (symbol -> latest mark/bid/ask/iv), fed by the chain WS + marks poll
const marks = new Map<string, { mark: number; bid: number; ask: number; iv: number }>();
const spots = new Map<string, number>(); // underlying -> latest spot (for notional fee)
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
  clearLegs: () => void;
  placeStrategy: (name: string, opts: { target: number | null; stop: number | null; autoExit: boolean; margin?: number; badge?: "matched" | "est" | "stale" }) => void;
  closePosition: (id: string, reason?: string) => void;
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
        // merge live marks + spot
        for (const r of chain.rows) {
          for (const c of [r.call, r.put]) marks.set(c.symbol, { mark: c.mark, bid: c.bid, ask: c.ask, iv: c.iv });
        }
        spots.set(chain.underlying, chain.spot);
        const s = get();
        // recompute open positions MTM + auto-exit
        const now = Date.now();
        const toExit: { id: string; reason: string }[] = [];
        const positions = s.positions.map((p) => {
          if (p.status === "closed") return p;
          const pnl = netPnl(p.legs, markRecord(p.legs));
          if (p.autoExit) {
            if (p.targetPnl != null && pnl >= p.targetPnl) toExit.push({ id: p.id, reason: "target" });
            else if (p.stopPnl != null && pnl <= p.stopPnl) toExit.push({ id: p.id, reason: "stop" });
          }
          // sample the MTM at most once per second (keeps ~15 min of per-second points)
      const last = p.mtm[p.mtm.length - 1];
      const mtm = now - last.t >= 1000 ? [...p.mtm, { t: now, pnl }].slice(-900) : p.mtm;
      return { ...p, mtm };
        });
        set({
          chain,
          expiries,
          expiry: s.expiry ?? chain.expiry,
          conn: "live",
          tickN: s.tickN + 1,
          positions,
        });
        for (const e of toExit) get().closePosition(e.id, e.reason);
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
        stopPnl: opts.stop,
        autoExit: opts.autoExit,
        mtm: [{ t: Date.now(), pnl: 0 }],
        notes: [],
      };
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
  const data = await fetchMarks(syms);
  for (const [sym, m] of Object.entries(data)) marks.set(sym, m);
  const now = Date.now();
  const toExit: { id: string; reason: string }[] = [];
  useStore.setState((s) => ({
    tickN: s.tickN + 1,
    positions: s.positions.map((p) => {
      if (p.status === "closed") return p;
      const pnl = netPnl(p.legs, markRecord(p.legs));
      if (p.autoExit) {
        if (p.targetPnl != null && pnl >= p.targetPnl) toExit.push({ id: p.id, reason: "target" });
        else if (p.stopPnl != null && pnl <= p.stopPnl) toExit.push({ id: p.id, reason: "stop" });
      }
      // sample the MTM at most once per second (keeps ~15 min of per-second points)
      const last = p.mtm[p.mtm.length - 1];
      const mtm = now - last.t >= 1000 ? [...p.mtm, { t: now, pnl }].slice(-900) : p.mtm;
      return { ...p, mtm };
    }),
  }));
  for (const e of toExit) useStore.getState().closePosition(e.id, e.reason);
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
