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
  targetPnl: number | null;
  stopPnl: number | null;
  autoExit: boolean;
  mtm: { t: number; pnl: number }[];
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
