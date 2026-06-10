"use client";

import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";
import {
  type PlaceLegIn,
  type ServerState,
  type StateTick,
  addNoteApi,
  closeLegApi,
  closePositionApi,
  connectChain,
  connectState,
  fetchExpiries,
  fetchFeed,
  fetchState,
  placeStrategy as placeStrategyApi,
  setLegRiskApi,
  setPositionRiskApi,
} from "./api";
import type {
  Contract,
  LedgerEntry,
  Leg,
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
// Display-only default until the server state hydrates (server seeds the same).
const START_BALANCE = 5000;
// Retain the live per-second series the server streams (12h safety ceiling) so
// the 1m/5m timeframe charts can aggregate the whole life of the trade.
const MTM_CAP = 12 * 60 * 60;
export const USDINR = 85; // Delta uses a fixed 1 USD = 85 INR

export type Currency = "USD" | "INR";

// Live chain feed (symbol -> latest mark/bid/ask/iv + per-option greeks), fed by
// the chain WS. Used ONLY for the builder/basket PREVIEW (live premium of unplaced
// legs) and the payoff "now" baseline — placed positions get their live values
// from the server state tick. Greeks are read-only from Delta.
interface Mark {
  mark: number;
  bid: number;
  ask: number;
  iv: number;
  delta: number;
  gamma: number;
  theta: number;
  vega: number;
  ts: number;
}
const marks = new Map<string, Mark>();
const spots = new Map<string, number>(); // underlying -> latest spot (for notional fee)
const atmIvs = new Map<string, number>(); // `${underlying}|${expiry}` -> ATM mark IV

// still-open legs of a position (closed legs are realized; excluded from live PnL/fees)
const openLegs = (p: Position): Leg[] => p.legs.filter((l) => l.status === "open");

// --- server state plumbing -------------------------------------------------- //
let disconnectChain: (() => void) | null = null;
let stateDisconnect: (() => void) | null = null;
let feedTimer: ReturnType<typeof setInterval> | null = null;
let refetching = false;

function applyServerState(st: ServerState): void {
  // Positions/balance/ledger/logs are server-owned. Currency stays a client-only
  // display toggle, so we deliberately do NOT overwrite it from the server here.
  useStore.setState({
    positions: st.positions,
    balance: st.account.balance,
    ledger: st.ledger,
    logs: st.logs,
  });
}

async function hydrate(): Promise<void> {
  const st = await fetchState();
  if (st) applyServerState(st);
}

// Append one server sample to a position's series (~1s; throttle guards races
// between a refetch and an in-flight tick). Capped at MTM_CAP.
function appendSample(series: SeriesSample[], sample?: SeriesSample): SeriesSample[] {
  if (!sample) return series;
  const last = series[series.length - 1];
  if (last && sample.t - last.t < 950) return series;
  return [...series, sample].slice(-MTM_CAP);
}

function onStateTick(t: StateTick): void {
  const s = useStore.getState();
  const openIds = new Set(t.openIds);
  // server auto-exit (a known-open id vanished) or a position placed elsewhere
  // (an unknown open id) → resync the full state instead of merging.
  const closedHere = s.positions.some((p) => p.status === "open" && !openIds.has(p.id));
  const placedElsewhere = t.openIds.some((id) => !s.positions.some((p) => p.id === id));
  if ((closedHere || placedElsewhere) && !refetching) {
    refetching = true;
    hydrate().finally(() => {
      refetching = false;
    });
    return;
  }
  const byId = new Map(t.positions.map((p) => [p.id, p]));
  const positions = s.positions.map((p) => {
    const live = byId.get(p.id);
    if (!live) return p; // closed positions aren't in the tick
    const { sample, ...fields } = live;
    return { ...p, ...fields, series: appendSample(p.series, sample) };
  });
  useStore.setState({ positions, balance: t.balance, tickN: s.tickN + 1 });
}

async function pollFeed(): Promise<void> {
  const f = await fetchFeed();
  useStore.setState({ feedFresh: f.fresh, feedAge: f.ageSeconds });
}

// camelCase risk patches -> the server's snake_case; only keys PRESENT in the
// patch are forwarded (the server uses model_fields_set to tell "set null" from
// "leave unchanged").
function posRiskPatch(patch: Partial<Pick<Position, "targetPnl" | "stopLossAmount" | "stopLossPctOfMargin" | "autoExit">>) {
  const out: Record<string, unknown> = {};
  if ("targetPnl" in patch) out.target_pnl = patch.targetPnl;
  if ("stopLossAmount" in patch) out.stop_loss_amount = patch.stopLossAmount;
  if ("stopLossPctOfMargin" in patch) out.stop_loss_pct_of_margin = patch.stopLossPctOfMargin;
  if ("autoExit" in patch) out.auto_exit = patch.autoExit;
  return out;
}
function legRiskPatch(patch: Partial<Pick<Leg, "targetPnl" | "stopPnl" | "autoExit" | "closeScope">>) {
  const out: Record<string, unknown> = {};
  if ("targetPnl" in patch) out.target_pnl = patch.targetPnl;
  if ("stopPnl" in patch) out.stop_pnl = patch.stopPnl;
  if ("autoExit" in patch) out.auto_exit = patch.autoExit;
  if ("closeScope" in patch) out.close_scope = patch.closeScope;
  return out;
}

interface State {
  underlying: Underlying;
  expiry: string | null;
  chain: OptionChain | null;
  expiries: string[];
  conn: "connecting" | "live" | "down";
  feedFresh: boolean; // Delta market-data feed is live (not frozen/disconnected)
  feedAge: number | null; // seconds since the last Delta message
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

// Build an unplaced basket leg. `entry` is a snapshot fallback only — the live
// builder preview reads legBasketPrice(), and the SERVER sets the real fill at
// execute time from the then-current quote.
function newLeg(c: Contract, side: Side, chain: OptionChain, u: Underlying): Leg {
  return {
    id: uid(),
    symbol: c.symbol,
    productId: c.productId,
    underlying: u,
    type: c.type,
    strike: c.strike,
    contractValue: c.contractValue,
    expiry: chain.expiry,
    dte: chain.dte,
    side,
    qty: 1,
    entry: side === "buy" ? c.ask || c.mark : c.bid || c.mark,
    markAtEntry: c.mark,
    spotAtEntry: chain.spot,
    targetPnl: null,
    stopPnl: null,
    autoExit: false,
    closeScope: "leg",
    status: "open",
    exitPrice: null,
    exitAt: null,
    exitReason: null,
    exitGross: null,
    exitFees: null,
  };
}

export const useStore = create<State>()(
  persist(
    (set, get) => ({
      underlying: "BTC",
      expiry: null,
      chain: null,
      expiries: [],
      conn: "connecting",
      feedFresh: true, // optimistic until the first feed poll
      feedAge: null,
      tickN: 0,
      selected: [],
      positions: [],
      balance: START_BALANCE,
      currency: "USD",
      ledger: [],
      logs: [],

      connect: () => {
        disconnectChain?.();
        set({ conn: "connecting" });
        const { underlying, expiry } = get();
        fetchExpiries(underlying).then((e) => set({ expiries: e })).catch(() => {});
        disconnectChain = connectChain(
          underlying,
          expiry,
          ({ chain, expiries }) => {
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
            // bump tickN so the live basket preview (legBasketPrice) re-renders
            set({ chain, expiries, expiry: s.expiry ?? chain.expiry, conn: "live", tickN: s.tickN + 1 });
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
        set((s) => (s.chain ? { selected: [...s.selected, newLeg(c, side, s.chain, s.underlying)] } : s)),
      // set-or-add: if this contract is already in the basket, set its side; else add
      selectLeg: (c, side) =>
        set((s) => {
          if (!s.chain) return s;
          const entry = side === "buy" ? c.ask || c.mark : c.bid || c.mark;
          if (s.selected.some((l) => l.symbol === c.symbol)) {
            return { selected: s.selected.map((l) => (l.symbol === c.symbol ? { ...l, side, entry } : l)) };
          }
          return { selected: [...s.selected, newLeg(c, side, s.chain, s.underlying)] };
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

      // Server fills each leg at the live quote at execute time and computes
      // margin/badge itself — opts.margin/badge are ignored (kept for signature
      // compatibility). On success the returned full state replaces ours.
      placeStrategy: async (name, opts) => {
        const s = get();
        if (s.selected.length === 0) return;
        const legs: PlaceLegIn[] = s.selected.map((l) => ({
          symbol: l.symbol, product_id: l.productId, underlying: l.underlying, type: l.type,
          strike: l.strike, contract_value: l.contractValue, expiry: l.expiry, dte: l.dte,
          side: l.side, qty: l.qty,
        }));
        const st = await placeStrategyApi({
          name,
          legs,
          target_pnl: opts.target,
          stop_loss_amount: opts.stopLossAmount,
          stop_loss_pct_of_margin: opts.stopLossPctOfMargin,
          auto_exit: opts.autoExit,
        });
        if (st) applyServerState(st);
      },

      closePosition: async (id, reason) => {
        const st = await closePositionApi(id, reason);
        if (st) applyServerState(st);
      },
      closeLeg: async (id, legId, reason) => {
        const st = await closeLegApi(id, legId, reason);
        if (st) applyServerState(st);
      },
      setPositionStop: async (id, patch) => {
        const st = await setPositionRiskApi(id, posRiskPatch(patch));
        if (st) applyServerState(st);
      },
      setPositionLegRisk: async (id, legId, patch) => {
        const st = await setLegRiskApi(legId, legRiskPatch(patch));
        if (st) applyServerState(st);
      },
      addNote: async (id, kind, body) => {
        const st = await addNoteApi(id, kind, body);
        if (st) applyServerState(st);
      },
    }),
    {
      name: "paper-trader-v1",
      version: 2,
      storage: createJSONStorage(() => localStorage),
      // Server owns positions/balance/ledger/logs now — persist ONLY UI prefs (the
      // builder basket + view selection + currency toggle). v2 migrate drops any
      // legacy persisted positions/account so we cleanly start from server state.
      // NOTE: the builder basket (`selected`) is deliberately NOT persisted — it's a
      // browser-local draft, so persisting it would leak one user's draft to whoever
      // logs in next on the same browser. Only view prefs persist.
      partialize: (s) => ({
        underlying: s.underlying,
        expiry: s.expiry,
        currency: s.currency,
      }),
      // default shallow merge applies this over the initial state, so a partial is
      // fine at runtime; cast to satisfy the migrate signature.
      migrate: (persisted) => {
        const p = (persisted ?? {}) as Partial<State>;
        return {
          selected: p.selected ?? [],
          underlying: p.underlying ?? "BTC",
          expiry: p.expiry ?? null,
          currency: p.currency ?? "USD",
        } as unknown as State;
      },
    },
  ),
);

// Hydrate server state + open the chain WS (basket preview) + the state WS (live
// positions) + the feed poll (stale guard). Returns a teardown fn.
export function startStream(): () => void {
  hydrate();
  useStore.getState().connect();
  if (!stateDisconnect) stateDisconnect = connectState(onStateTick);
  if (!feedTimer) {
    pollFeed();
    feedTimer = setInterval(pollFeed, 2000);
  }
  return () => {
    disconnectChain?.();
    disconnectChain = null;
    stateDisconnect?.();
    stateDisconnect = null;
    if (feedTimer) {
      clearInterval(feedTimer);
      feedTimer = null;
    }
  };
}

// --- live read helpers (used by components) --------------------------------- //
// Placed-position legs carry server live values (leg.mark/iv/pnl). Unplaced basket
// legs fall back to the live chain feed, then to the entry snapshot.
export function legMark(leg: Leg): number {
  return leg.mark ?? marks.get(leg.symbol)?.mark ?? leg.entry;
}
export function legIv(leg: Leg): number {
  return leg.iv ?? marks.get(leg.symbol)?.iv ?? 0;
}
// Live would-fill premium for an UNPLACED basket leg (buy crosses to ask, sell to
// bid) — drives the builder's real-time premium/net until the order is executed.
export function legBasketPrice(leg: Leg): number {
  const m = marks.get(leg.symbol);
  if (!m) return leg.entry;
  return leg.side === "buy" ? m.ask || m.mark || leg.entry : m.bid || m.mark || leg.entry;
}
export function spotOf(u: Underlying): number {
  return spots.get(u) ?? 0;
}
export function positionPnl(p: Position): number {
  return p.pnl ?? 0;
}
// Cost of crossing the spread on entry (USD) — computed from the leg's STATIC
// entry/markAtEntry, so it's stable regardless of live marks.
export function entrySlippage(p: Position): number {
  return openLegs(p).reduce(
    (s, l) => s + Math.abs(l.entry - l.markAtEntry) * l.qty * l.contractValue,
    0,
  );
}

// Delta India options fee = min(rate·notional, cap·premium), then optional
// referral discount, then +18% GST. Per leg, on entry AND exit.
// notional = spot·cv·qty, premium = price·cv·qty.
//
// The AUTHORITATIVE fee/fill/margin model now lives server-side (backend money
// engine). These constants + entryFee remain client-side ONLY to render the
// entry-fee line + tooltip, computed from the leg's static entry data (no live
// marks). Verified to the cent against Delta's calculator; mirrors the server.
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
  return openLegs(p).reduce((s, l) => s + legFee(l.entry, l.spotAtEntry, l.contractValue, l.qty), 0);
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
