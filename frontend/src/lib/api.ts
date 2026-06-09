// Real Delta data via the backend: all expiries + a live chain WebSocket.
// Maps the backend (/v2/tickers-derived) shape into the UI types.

import type { ChainRow, Contract, OptionChain, Underlying } from "./types";

const API = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8010";
const WS = process.env.NEXT_PUBLIC_WS_BASE ?? "ws://localhost:8010";

const num = (x: unknown): number => (typeof x === "number" ? x : Number(x) || 0);

function dteFrom(expiry: string): number {
  const ms = new Date(expiry + "T12:00:00Z").getTime() - Date.now();
  return Math.max(0, Math.round(ms / 86_400_000));
}

interface BC {
  symbol: string;
  product_id: number;
  option_type: "call" | "put";
  strike: number;
  mark_price: number | null;
  last_price: number | null;
  oi: number | null;
  oi_value_usd: number | null;
  contract_value: number | null;
  quote: { bid: number | null; ask: number | null; mark_iv: number | null };
  greeks: {
    delta: number | null;
    gamma: number | null;
    theta: number | null;
    vega: number | null;
  };
}

function mapContract(c: BC | null): Contract | null {
  if (!c) return null;
  return {
    symbol: c.symbol,
    productId: c.product_id,
    type: c.option_type,
    strike: c.strike,
    mark: num(c.mark_price),
    ltp: num(c.last_price),
    bid: num(c.quote?.bid),
    ask: num(c.quote?.ask),
    iv: num(c.quote?.mark_iv),
    oi: num(c.oi),
    oiValueUsd: num(c.oi_value_usd),
    contractValue: num(c.contract_value) || 0.001,
    greeks: {
      delta: num(c.greeks?.delta),
      gamma: num(c.greeks?.gamma),
      theta: num(c.greeks?.theta),
      vega: num(c.greeks?.vega),
    },
  };
}

interface BackChain {
  underlying: Underlying;
  expiry: string;
  spot: number;
  atm_strike: number;
  atm_iv: number;
  rows: { strike: number; call: BC | null; put: BC | null }[];
}

export function mapChain(c: BackChain): OptionChain {
  const rows: ChainRow[] = c.rows
    .map((r) => ({
      strike: r.strike,
      call: mapContract(r.call),
      put: mapContract(r.put),
    }))
    .filter((r): r is ChainRow => r.call !== null && r.put !== null);
  return {
    underlying: c.underlying,
    expiry: c.expiry,
    dte: dteFrom(c.expiry),
    spot: num(c.spot),
    atmStrike: num(c.atm_strike),
    atmIv: num(c.atm_iv),
    rows,
  };
}

export async function fetchExpiries(u: Underlying): Promise<string[]> {
  const r = await fetch(`${API}/api/chain/expiries?underlying=${u}`);
  return (await r.json()).expiries as string[];
}

export interface ChainMsg {
  chain: OptionChain;
  expiries: string[];
}

/** Connect the live chain WS. Returns a disconnect fn. */
export function connectChain(
  u: Underlying,
  expiry: string | null,
  onMsg: (m: ChainMsg) => void,
  onState?: (s: "live" | "down") => void,
): () => void {
  let closed = false;
  const ws = new WebSocket(`${WS}/ws/chain`);
  ws.onopen = () => {
    onState?.("live");
    ws.send(JSON.stringify({ underlying: u, expiry }));
  };
  ws.onmessage = (e) => {
    const m = JSON.parse(e.data);
    if (m.type === "chain")
      onMsg({ chain: mapChain(m.chain), expiries: m.expiries });
  };
  ws.onclose = () => !closed && onState?.("down");
  ws.onerror = () => onState?.("down");
  return () => {
    closed = true;
    ws.close();
  };
}

export interface BookLevel {
  price: number;
  size: number;
}

export async function fetchOrderbook(
  symbol: string,
): Promise<{ buy: BookLevel[]; sell: BookLevel[] }> {
  try {
    const r = await fetch(`${API}/api/orderbook?symbol=${encodeURIComponent(symbol)}`);
    const d = await r.json();
    const map = (a: { price: unknown; size: unknown }[]) =>
      (a || []).map((x) => ({ price: num(x.price), size: num(x.size) }));
    return { buy: map(d.buy), sell: map(d.sell) };
  } catch {
    return { buy: [], sell: [] };
  }
}

export async function fetchMarks(
  symbols: string[],
): Promise<Record<string, { mark: number; bid: number; ask: number; iv: number }>> {
  if (symbols.length === 0) return {};
  try {
    const r = await fetch(`${API}/api/marks?symbols=${encodeURIComponent(symbols.join(","))}`);
    return await r.json();
  } catch {
    return {};
  }
}

export async function fetchMargin(
  underlying: Underlying,
  legs: { product_id: number; side: "buy" | "sell"; size: number }[],
): Promise<{ margin: number; badge: string; source: string } | null> {
  try {
    const r = await fetch(`${API}/api/margin`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ underlying, legs }),
    });
    if (!r.ok) return null;
    return await r.json();
  } catch {
    return null;
  }
}
