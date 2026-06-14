// Real Delta data via the backend: all expiries + a live chain WebSocket.
// Maps the backend (/v2/tickers-derived) shape into the UI types.

import { getAccessToken } from "./auth";
import type {
  ChainRow,
  Contract,
  LedgerEntry,
  Leg,
  LogEntry,
  OptionChain,
  Position,
  SeriesSample,
  Side,
  Underlying,
} from "./types";

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
  let ws: WebSocket | null = null;
  let retry: ReturnType<typeof setTimeout> | null = null;
  let backoff = 1000;
  const schedule = () => {
    if (closed || retry) return;
    retry = setTimeout(() => { retry = null; connect(); }, backoff);
    backoff = Math.min(backoff * 2, 15000); // 1s → 2s → … → 15s cap
  };
  const connect = () => {
    ws = new WebSocket(`${WS}/ws/chain`);
    ws.onopen = () => {
      backoff = 1000; // reset on a healthy connection
      onState?.("live");
      ws?.send(JSON.stringify({ underlying: u, expiry }));
    };
    ws.onmessage = (e) => {
      const m = JSON.parse(e.data);
      if (m.type === "chain") onMsg({ chain: mapChain(m.chain), expiries: m.expiries });
    };
    // a dropped socket (network blip, tab backgrounded) must auto-reconnect, not stay "down"
    ws.onclose = () => { if (closed) return; onState?.("down"); schedule(); };
    ws.onerror = () => { try { ws?.close(); } catch { /* onclose will fire → reconnect */ } };
  };
  connect();
  // mobile: reconnect immediately when the tab returns to the foreground — phones throttle
  // the backoff timer while backgrounded, so a plain timer can be slow to fire on resume.
  const onVis = () => {
    if (closed || typeof document === "undefined" || document.visibilityState !== "visible") return;
    if (ws && ws.readyState <= WebSocket.OPEN) return; // still connecting/open → leave it
    if (retry) { clearTimeout(retry); retry = null; }
    backoff = 1000;
    connect();
  };
  if (typeof document !== "undefined") document.addEventListener("visibilitychange", onVis);
  return () => {
    closed = true;
    if (retry) clearTimeout(retry);
    if (typeof document !== "undefined") document.removeEventListener("visibilitychange", onVis);
    ws?.close();
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

export interface MarkData {
  mark: number;
  bid: number;
  ask: number;
  iv: number;
  delta: number;
  gamma: number;
  theta: number;
  vega: number;
}

export async function fetchMarks(symbols: string[]): Promise<Record<string, MarkData>> {
  if (symbols.length === 0) return {};
  try {
    const r = await fetch(`${API}/api/marks?symbols=${encodeURIComponent(symbols.join(","))}`);
    return await r.json();
  } catch {
    return {};
  }
}

// ATM mark-IV per "<underlying>|<expiry>" for held positions (read-only). One
// request per distinct underlying; results merged into a flat keyed map.
export async function fetchAtmIv(
  pairs: { underlying: Underlying; expiry: string }[],
): Promise<Record<string, number>> {
  const byU = new Map<Underlying, Set<string>>();
  for (const p of pairs) {
    if (!p.expiry) continue;
    let set = byU.get(p.underlying);
    if (!set) byU.set(p.underlying, (set = new Set()));
    set.add(p.expiry);
  }
  const out: Record<string, number> = {};
  await Promise.all(
    [...byU.entries()].map(async ([u, exps]) => {
      try {
        const r = await fetch(
          `${API}/api/atm-iv?underlying=${u}&expiries=${encodeURIComponent([...exps].join(","))}`,
        );
        const d = (await r.json()) as Record<string, number | null>;
        for (const [e, iv] of Object.entries(d)) out[`${u}|${e}`] = num(iv);
      } catch {
        /* leave missing — chart shows 0 until next poll */
      }
    }),
  );
  return out;
}

// Delta-feed freshness for the stale-data guard. Backend unreachable → treat as stale.
export async function fetchFeed(): Promise<{ connected: boolean; ageSeconds: number | null; fresh: boolean }> {
  try {
    const r = await fetch(`${API}/api/feed`);
    const d = await r.json();
    return { connected: !!d.connected, ageSeconds: d.age_seconds ?? null, fresh: !!d.fresh };
  } catch {
    return { connected: false, ageSeconds: null, fresh: false };
  }
}

export interface PayoffLegIn {
  option_type: "call" | "put";
  side: "buy" | "sell";
  qty: number;
  strike: number;
  entry: number;
  iv: number;
  contract_value: number;
  t_years: number; // this leg's time-to-expiry from now (years) — enables calendars
}
export interface ExpiryCurve {
  tYears: number;
  pnl: number[];
}
export interface PayoffData {
  spots: number[];
  expiries: ExpiryCurve[]; // one at-expiry curve per distinct leg expiry (nearest first)
  expiry: number[]; // primary (front) expiry curve
  projected: number[];
  greeks: { delta: number; gamma: number; theta: number; vega: number };
  breakevens: number[];
  max_profit: number;
  max_loss: number;
}

// Analyse Payoff: per-expiry curves + projected (what-if date/IV) curve + net greeks.
export async function fetchPayoff(req: {
  legs: PayoffLegIn[];
  spot: number;
  lo: number;
  hi: number;
  points: number;
  elapsed_years: number; // time from now to the target scenario date (0 = now)
  iv_shift: number;
}): Promise<PayoffData | null> {
  try {
    const r = await fetch(`${API}/api/payoff`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
    });
    if (!r.ok) return null;
    return await r.json();
  } catch {
    return null;
  }
}

export interface MarginQuote {
  margin: number; // the order margin used (exact when matched, else the calibrated estimate)
  badge: string; // "matched" | "est" | "stale"
  source: string;
  localMargin: number | null; // raw local Black-76 estimate (always computed)
  divergence: number | null; // (local − exact)/exact %, when matched
  calibrationFactor: number | null; // learned local→exact correction in effect
}
export async function fetchMargin(
  underlying: Underlying,
  legs: { product_id: number; side: "buy" | "sell"; size: number }[],
): Promise<MarginQuote | null> {
  try {
    const r = await fetch(`${API}/api/margin`, {
      method: "POST",
      headers: await authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ underlying, legs }),
    });
    if (!r.ok) return null;
    const d = await r.json();
    return {
      margin: d.margin,
      badge: d.badge,
      source: d.source,
      localMargin: d.local_margin ?? null,
      divergence: d.divergence_pct ?? null,
      calibrationFactor: d.calibration_factor ?? null,
    };
  } catch {
    return null;
  }
}

// --- server-authoritative sim state ---------------------------------------- //
// The backend owns positions/balance/ledger/logs (Postgres+Timescale). All
// mutations return the FRESH full state; a WS pushes a ~1s live tick. Shapes are
// camelCase + epoch-ms, matching ./types directly (see backend/app/sim/service.py).

export interface Account {
  balance: number;
  currency: string;
  startBalance: number;
}
export interface ServerState {
  account: Account;
  positions: Position[];
  ledger: LedgerEntry[];
  logs: LogEntry[];
}

export interface PlaceLegIn {
  symbol: string;
  product_id: number;
  underlying: Underlying;
  type: "call" | "put";
  strike: number;
  contract_value: number;
  expiry: string;
  dte: number;
  side: Side;
  qty: number;
}
export interface PlaceStrategyIn {
  name: string;
  legs: PlaceLegIn[];
  target_pnl: number | null;
  stop_loss_amount: number | null;
  stop_loss_pct_of_margin: number | null;
  auto_exit: boolean;
}

// Attach the Supabase access token so the backend resolves the authenticated user
// (in dev with no token the backend falls back to the stub user).
async function authHeaders(extra?: Record<string, string>): Promise<Record<string, string>> {
  const token = await getAccessToken();
  return { ...(extra ?? {}), ...(token ? { Authorization: `Bearer ${token}` } : {}) };
}

async function postJson(path: string, body?: unknown): Promise<ServerState | null> {
  try {
    const r = await fetch(`${API}${path}`, {
      method: body && (body as { __patch?: boolean }).__patch ? "PATCH" : "POST",
      headers: await authHeaders({ "Content-Type": "application/json" }),
      body: body ? JSON.stringify(stripMeta(body)) : undefined,
    });
    if (!r.ok) return null;
    return (await r.json()) as ServerState;
  } catch {
    return null;
  }
}
function stripMeta(b: unknown): unknown {
  if (b && typeof b === "object" && "__patch" in (b as object)) {
    const rest: Record<string, unknown> = { ...(b as Record<string, unknown>) };
    delete rest.__patch;
    return rest;
  }
  return b;
}

export async function fetchState(): Promise<ServerState | null> {
  try {
    const r = await fetch(`${API}/api/state`, { headers: await authHeaders() });
    if (!r.ok) return null;
    return (await r.json()) as ServerState;
  } catch {
    return null;
  }
}

// One position's MTM/IV/greeks history, loaded lazily when its detail/charts open.
// The server already chose the resolution (uniform-by-age body + 1s live tail) and
// stamps OHLC per point — so the client renders it directly (no re-bucketing).
export async function fetchPositionSeries(id: string): Promise<SeriesSample[] | null> {
  try {
    const r = await fetch(`${API}/api/positions/${id}/series`, { headers: await authHeaders() });
    if (!r.ok) return null;
    const j = (await r.json()) as { series: SeriesSample[] };
    return j.series;
  } catch {
    return null;
  }
}

export const placeStrategy = (req: PlaceStrategyIn): Promise<ServerState | null> =>
  postJson(`/api/strategies`, req);

export const closePositionApi = (id: string, reason?: string): Promise<ServerState | null> =>
  postJson(`/api/strategies/${id}/close`, { reason: reason ?? null });

export const closeLegApi = (id: string, legId: string, reason?: string): Promise<ServerState | null> =>
  postJson(`/api/strategies/${id}/legs/${legId}/close`, { reason: reason ?? null });

// PATCH risk — only the keys present in `patch` are sent (server uses model_fields_set
// to distinguish "set to null" from "leave unchanged"); __patch flags the verb.
export const setPositionRiskApi = (
  id: string,
  patch: Partial<{ target_pnl: number | null; stop_loss_amount: number | null; stop_loss_pct_of_margin: number | null; auto_exit: boolean; stale_hard_stop: boolean }>,
): Promise<ServerState | null> => postJson(`/api/strategies/${id}/risk`, { ...patch, __patch: true });

export const setLegRiskApi = (
  legId: string,
  patch: Partial<{ target_pnl: number | null; stop_pnl: number | null; auto_exit: boolean; close_scope: string }>,
): Promise<ServerState | null> => postJson(`/api/legs/${legId}/risk`, { ...patch, __patch: true });

export const addNoteApi = (id: string, kind: "entry" | "exit", body: string): Promise<ServerState | null> =>
  postJson(`/api/strategies/${id}/notes`, { kind, body });

export interface StateTick {
  type: "tick";
  balance: number;
  currency: string;
  openIds: string[];
  positions: (Partial<Position> & { id: string; sample?: import("./types").SeriesSample })[];
}

/** Connect the live state WS (~1s tick). Returns a disconnect fn. */
export function connectState(
  onTick: (t: StateTick) => void,
  onState?: (s: "live" | "down") => void,
): () => void {
  let closed = false;
  let ws: WebSocket | null = null;
  let retry: ReturnType<typeof setTimeout> | null = null;
  let backoff = 1000;
  const schedule = () => {
    if (closed || retry) return;
    retry = setTimeout(() => { retry = null; connect(); }, backoff);
    backoff = Math.min(backoff * 2, 15000);
  };
  const connect = () => {
    // token rides in the Sec-WebSocket-Protocol handshake (not the URL → not logged);
    // re-fetched on each (re)connect so an expired token is refreshed on reconnect.
    getAccessToken().then((token) => {
      if (closed) return;
      ws = token
        ? new WebSocket(`${WS}/api/ws/state`, ["jwt", token])
        : new WebSocket(`${WS}/api/ws/state`);
      ws.onopen = () => { backoff = 1000; onState?.("live"); };
      ws.onmessage = (e) => {
        const m = JSON.parse(e.data);
        if (m.type === "tick") onTick(m as StateTick);
      };
      ws.onclose = () => { if (closed) return; onState?.("down"); schedule(); };
      ws.onerror = () => { try { ws?.close(); } catch { /* onclose reconnects */ } };
    }).catch(() => schedule());
  };
  connect();
  const onVis = () => {
    if (closed || typeof document === "undefined" || document.visibilityState !== "visible") return;
    if (ws && ws.readyState <= WebSocket.OPEN) return;
    if (retry) { clearTimeout(retry); retry = null; }
    backoff = 1000;
    connect();
  };
  if (typeof document !== "undefined") document.addEventListener("visibilitychange", onVis);
  return () => {
    closed = true;
    if (retry) clearTimeout(retry);
    if (typeof document !== "undefined") document.removeEventListener("visibilitychange", onVis);
    ws?.close();
  };
}

// --- account / settings / admin (all authed) ------------------------------- //
async function authedJson<T>(
  path: string,
  init?: { method?: string; body?: unknown },
): Promise<T | null> {
  try {
    const r = await fetch(`${API}${path}`, {
      method: init?.method ?? "GET",
      headers: await authHeaders(init?.body ? { "Content-Type": "application/json" } : undefined),
      body: init?.body ? JSON.stringify(init.body) : undefined,
    });
    if (!r.ok) return null;
    return (await r.json()) as T;
  } catch {
    return null;
  }
}

export interface Me {
  email: string;
  displayName: string;
  isAdmin: boolean;
  hasLiveKeys: boolean; // holds live Delta trade keys → 2FA required at every login
}
export const fetchMe = (): Promise<Me | null> => authedJson<Me>("/api/me");

// vault slots → boolean "is set" (plaintext never leaves the server)
export type KeyStatus = Record<string, boolean>;
export const fetchKeys = (): Promise<KeyStatus | null> => authedJson<KeyStatus>("/api/settings/keys");
export const saveKeys = (body: Record<string, string>): Promise<KeyStatus | null> =>
  authedJson<KeyStatus>("/api/settings/keys", { method: "PUT", body });

export interface AdminUser {
  id: string;
  email: string;
  displayName: string;
  isAdmin: boolean;
  isActive: boolean;
  hasLogin: boolean;
  createdAt: number | null;
}
export const fetchAdminUsers = (): Promise<AdminUser[] | null> =>
  authedJson<AdminUser[]>("/api/admin/users");

export interface AllowlistEntry {
  email: string;
  invitedBy: string | null;
  createdAt: number | null;
}
export const fetchAllowlist = (): Promise<AllowlistEntry[] | null> =>
  authedJson<AllowlistEntry[]>("/api/admin/allowlist");
export const addAllowlist = (email: string): Promise<{ email: string } | null> =>
  authedJson<{ email: string }>("/api/admin/allowlist", { method: "POST", body: { email } });
export const removeAllowlist = (email: string): Promise<{ removed: string } | null> =>
  authedJson<{ removed: string }>(`/api/admin/allowlist/${encodeURIComponent(email)}`, { method: "DELETE" });

export interface UserLog {
  t: number;
  action: string;
  detail: string;
  tone: string | null;
}
export const fetchUserLogs = (id: string): Promise<UserLog[] | null> =>
  authedJson<UserLog[]>(`/api/admin/users/${id}/logs`);

// Builder draft basket — server-side per user, so it follows the user across devices.
export const fetchDraft = (): Promise<{ legs: Leg[] } | null> =>
  authedJson<{ legs: Leg[] }>("/api/draft");
export const saveDraft = (legs: Leg[]): Promise<{ ok: boolean } | null> =>
  authedJson<{ ok: boolean }>("/api/draft", { method: "PUT", body: { legs } });
