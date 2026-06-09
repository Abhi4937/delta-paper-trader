import type { Greeks, OptType } from "./bs";

export type Underlying = "BTC" | "ETH";

export interface Contract {
  symbol: string;
  productId: number;
  type: OptType;
  strike: number;
  mark: number;
  ltp: number;
  bid: number;
  ask: number;
  iv: number;
  oi: number;
  oiValueUsd: number; // OI notional in USD (Delta's "OI" column)
  contractValue: number; // BTC per contract (e.g. 0.001)
  greeks: Greeks;
}

export interface ChainRow {
  strike: number;
  call: Contract;
  put: Contract;
}

export interface OptionChain {
  underlying: Underlying;
  expiry: string; // ISO date
  dte: number;
  spot: number;
  atmStrike: number;
  atmIv: number;
  rows: ChainRow[];
}

export type Side = "buy" | "sell";

export interface Leg {
  id: string;
  symbol: string;
  productId: number;
  underlying: Underlying;
  type: OptType;
  strike: number;
  contractValue: number;
  expiry: string;
  dte: number;
  side: Side;
  qty: number;
  entry: number; // entry fill premium (crosses the spread → includes entry slippage)
  markAtEntry: number; // mark at the moment of entry (for entry-slippage display)
  spotAtEntry: number; // underlying spot at entry (for notional-based fee)
  // per-leg risk/exit (TP/SL act on the leg's PnL in $)
  targetPnl: number | null;
  stopPnl: number | null;
  autoExit: boolean; // auto-exit this leg when its TP/SL is hit
  closeScope: "leg" | "strategy"; // on trigger: close this leg only, or the whole strategy
  status: "open" | "closed"; // legs can be closed independently
  // exit record (set when the leg is closed)
  exitPrice: number | null; // exit fill premium (crosses the spread)
  exitAt: number | null; // close timestamp
  exitReason: string | null; // "manual" | "leg TP/SL" | "combined SL" | ...
  exitGross: number | null; // gross PnL at exit fill (before fees)
  exitFees: number | null; // entry + exit fee for this leg
}

// One sampled MTM/IV/greeks row for a position (taken ~1/sec from open). Net
// values are aggregated (signed_qty x contractValue x per-option greek); per-leg
// values are keyed by leg.id so leg lines sum to the net line.
export interface LegSample {
  pnl: number; // signed leg PnL (USD)
  iv: number; // leg mark IV (fraction, e.g. 0.45)
  delta: number; // signed leg position delta (BTC)
}
export interface SeriesSample {
  t: number;
  pnl: number; // net PnL (USD)
  delta: number; // net delta (BTC)
  theta: number; // net theta (USD/day)
  vega: number; // net vega (USD per 1 vol-point)
  atmIv: Record<string, number>; // ATM mark IV per leg-expiry present (ISO date → fraction)
  legs: Record<string, LegSample>;
}

export type MarginBadge = "matched" | "est" | "stale";

export interface Position {
  id: string;
  name: string;
  underlying: Underlying;
  expiry: string;
  legs: Leg[];
  margin: number;
  marginBadge: MarginBadge;
  openedAt: number;
  status: "open" | "closed";
  // combined net-capital stop: an absolute loss ($) OR a % of reserved margin
  targetPnl: number | null;
  stopLossAmount: number | null;
  stopLossPctOfMargin: number | null;
  autoExit: boolean; // combined auto-exit on
  autoExitSuspended: boolean; // runtime: stale marks → auto-exit paused
  closedAt: number | null; // when the whole strategy closed
  closeReason: string | null; // reason the strategy closed (manual / SL / ...)
  series: SeriesSample[]; // per-second net + per-leg MTM/IV/greeks, from open
  notes: { kind: "entry" | "exit"; body: string; at: number }[];
}

export interface LedgerEntry {
  t: number;
  type: "deposit" | "margin_reserve" | "margin_release" | "realized" | "fee";
  amount: number;
  balanceAfter: number;
  ref?: string;
}

export interface LogEntry {
  t: number;
  action: string;
  detail: string;
  tone?: "pos" | "neg" | "info" | "warn";
}
