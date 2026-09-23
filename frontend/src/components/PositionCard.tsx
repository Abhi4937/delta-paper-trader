"use client";

import { useEffect, useState } from "react";
import clsx from "clsx";
import { ChevronDown, ChevronRight, Download, LineChart, Power, RefreshCw, ShieldAlert, X } from "lucide-react";
import PayoffPanel from "@/components/PayoffPanel";
import PositionCharts from "@/components/PositionCharts";
import { type LiveCheck, armLiveGroup, exitLiveGroup } from "@/lib/api";
import { exportPositionXlsx } from "@/lib/exportXlsx";
import { legPnl } from "@/lib/engine";
import { combinedFloor } from "@/lib/exit";
import { legColorMap } from "@/lib/legColors";
import { ACTIVE_FEE, type Currency, USDINR, entryFee, entrySlippage, legIv, legMark, money, positionPnl, spotOf, useStore } from "@/lib/store";
import type { Position } from "@/lib/types";

// entry date + time, e.g. "09 Jun 14:36" (legs are placed at position open)
function fmtEntry(ms: number): string {
  const d = new Date(ms);
  return `${d.toLocaleDateString("en-GB", { day: "2-digit", month: "short" })} ${d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })}`;
}

export interface LiveProps {
  check?: LiveCheck;
  tradingEnabled: boolean;
  onChanged: () => void; // refetch live status after arm/exit
}

export default function PositionCard({
  p, currency, onClose, live,
}: { p: Position; currency: Currency; onClose?: (id: string) => void; live?: LiveProps }) {
  const pnl = positionPnl(p);
  const closed = p.status === "closed";
  const exp = p.expiry.slice(5);
  // each strategy collapses; closed strategies start collapsed
  const [open, setOpen] = useState(!closed);
  const [analyse, setAnalyse] = useState(false);
  // per-leg chart colors → swatch in the Symbol column maps rows to chart lines
  const legColors = legColorMap(p.legs);
  // GET /api/state ships positions with an empty series; lazily pull this position's
  // history (uniform-by-age body + 1s tail) the first time its detail/charts open.
  // Live WS ticks then keep appending to p.series, so we fetch once per open.
  const loadPositionSeries = useStore((s) => s.loadPositionSeries);
  useEffect(() => {
    if (open) loadPositionSeries(p.id);
  }, [open, p.id, loadPositionSeries]);

  return (
    <div className={clsx("rounded-lg border bg-surface", closed ? "border-line/60 opacity-60" : "border-line")}>
      {/* header */}
      <div className={clsx("flex flex-wrap items-center gap-x-3 gap-y-1.5 px-4 py-2.5", open && "border-b border-line/60")}>
        <button
          onClick={() => setOpen((o) => !o)}
          className="text-text-mute hover:text-text-dim"
          title={open ? "Collapse strategy" : "Expand strategy"}
        >
          {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </button>
        <span className="text-[13px] font-semibold text-text">{p.name}</span>
        <span className="rounded-[4px] bg-surface-3 px-1.5 py-0.5 text-[10px] text-text-dim">
          {p.underlying} · {exp}
        </span>
        <span
          className={clsx(
            "rounded-[3px] px-1 py-0.5 text-[9px] uppercase",
            p.marginBadge === "matched" ? "bg-pos/15 text-pos" : "bg-warn/15 text-warn",
          )}
        >
          {p.marginBadge === "matched" ? "exact" : p.marginBadge}
        </span>
        {p.autoExitSuspended && (
          <span
            className="rounded-[3px] bg-warn/15 px-1.5 py-0.5 text-[9px] uppercase text-warn"
            title="A leg's mark is stale (>10s old) — auto-exit is paused so we never exit on bad data."
          >
            auto-exit paused · stale
          </span>
        )}
        {live && !closed && (
          <span
            className={clsx(
              "rounded-[3px] px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wider",
              p.exiting ? "bg-neg/20 text-neg" : p.autoExit ? "bg-pos/15 text-pos" : "bg-surface-3 text-text-mute",
            )}
            title={p.autoExit ? "SL armed: server watches leg + basket SL; a reduce-only stop rests on Delta per leg" : "Tracking only — no SL will act"}
          >
            {p.exiting ? "exiting all legs" : p.autoExit ? "armed" : "tracking only"}
          </span>
        )}
        {closed && (p.closeReason === "settlement" || p.closeReason === "expiry-close" ? (
          <span
            className="rounded-[3px] bg-accent/15 px-1.5 py-0.5 text-[9px] uppercase tracking-wider text-accent"
            title={p.closeReason === "settlement"
              ? "All legs settled at Delta's published expiry settlement price (fee-free). Final settled state."
              : "Nearest expiry settled (fee-free); the remaining later-expiry legs were closed at market with exit fees."}
          >
            {p.closeReason === "settlement" ? "settled at expiry" : "closed at expiry"}
            {p.closedAt && <span className="ml-1 tnum normal-case">· {fmtEntry(p.closedAt)}</span>}
          </span>
        ) : (
          <span className="text-[10px] text-text-mute">
            <span className="rounded-[3px] bg-surface-3 px-1.5 py-0.5 uppercase tracking-wider">closed</span>
            {p.closeReason && <span className="ml-1 text-text-dim">· {p.closeReason}</span>}
            {p.closedAt && <span className="ml-1 tnum">· {fmtEntry(p.closedAt)}</span>}
          </span>
        ))}
        <div className="ml-auto flex flex-wrap items-center justify-end gap-x-4 gap-y-1.5 text-[11px]">
          <span className="text-text-mute">
            Margin <span className="tnum text-text-dim">{money(p.margin, currency)}</span>
          </span>
          {!live && (
            <>
              <span className="text-text-mute" title="cost of crossing the spread on entry (already in MTM)">
                Entry slip <span className="tnum text-warn">{money(entrySlippage(p), currency)}</span>
              </span>
              <span className="text-text-mute" title={`Delta options fee (${ACTIVE_FEE.name}): min(${(ACTIVE_FEE.notionalRate * 100).toFixed(3)}% notional, ${(ACTIVE_FEE.premiumCap * 100).toFixed(1)}% premium) +18% GST. Charged to realized PnL on close, not to MTM.`}>
                Entry fee <span className="tnum text-text-dim">{money(entryFee(p), currency)}</span>
                <span className="ml-1 text-[9px] text-accent">{ACTIVE_FEE.name}</span>
              </span>
            </>
          )}
          <span className="text-text-mute">
            UPNL{" "}
            <span className={clsx("tnum text-[13px] font-semibold", pnl >= 0 ? "text-pos" : "text-neg")}>
              {pnl >= 0 ? "+" : ""}{money(pnl, currency)}
            </span>
          </span>
          <button
            onClick={() => { setOpen(true); setAnalyse((a) => !a); }}
            className={clsx("flex items-center gap-1 rounded-[5px] border px-2 py-1 text-[11px]", analyse ? "border-accent text-accent" : "border-line text-text-mute hover:text-text")}
            title={closed ? "Analyse this strategy's MTM / IV / greeks charts over its life (entry → close)" : "Payoff what-if for this strategy (spot / date / IV scenarios)"}
          >
            <LineChart size={12} /> {closed ? "Analyse" : "Payoff"}
          </button>
          <button
            onClick={() => exportPositionXlsx(p)}
            className="flex items-center gap-1 rounded-[5px] border border-line px-2 py-1 text-[11px] text-text-mute hover:text-text"
            title="Download full strategy + every panel's chart data as Excel (.xlsx)"
          >
            <Download size={12} /> Excel
          </button>
          {!closed && live && <LiveControls p={p} live={live} />}
          {!closed && !live && onClose && (
            <button
              onClick={() => onClose(p.id)}
              className="flex items-center gap-1 rounded-[5px] border border-neg/40 px-2.5 py-1 text-[11px] font-semibold text-neg hover:bg-neg/10"
            >
              <X size={12} /> Close
            </button>
          )}
        </div>
      </div>

      {open && (
        <>
      {/* open positions: "Payoff" toggles the what-if panel */}
      {!closed && analyse && (
        <div className="border-b border-line/60 bg-surface-2/40 px-4 py-3">
          <div className="mx-auto max-w-[440px] rounded-lg border border-line bg-surface">
            <PayoffPanel legs={p.legs} />
          </div>
        </div>
      )}
      {/* legs — Delta-style columns (compact) */}
      <div className="overflow-x-auto px-4 py-0.5">
        <div className="grid min-w-[880px] grid-cols-[28px_minmax(140px,1.3fr)_104px_76px_84px_56px_76px_92px_84px_80px] gap-2 py-1 text-[9px] uppercase tracking-wider text-text-mute">
          <span>B/S</span><span>Symbol</span><span>Entry Time</span><span className="text-right">Size BTC</span>
          <span className="text-right">Notional</span><span className="text-right">Entry</span>
          <span className="text-right">Index</span><span className="text-right">Mark · IV</span>
          <span className="text-right">UPNL</span><span className="text-right">Cashflow</span>
        </div>
        {p.legs.map((l) => {
          const closed = l.status === "closed";
          const mark = legMark(l);
          const iv = legIv(l);
          const lpnl = closed ? (l.exitGross ?? 0) - (l.exitFees ?? 0) : legPnl(l, mark);
          const sign = l.side === "sell" ? -1 : 1;
          const sizeBtc = sign * l.qty * l.contractValue;
          const index = spotOf(l.underlying) || l.spotAtEntry;
          const notional = l.qty * l.contractValue * index;
          const cashflow = (l.side === "sell" ? 1 : -1) * l.entry * l.qty * l.contractValue;
          return (
            <div key={l.id} className={clsx("grid min-w-[880px] grid-cols-[28px_minmax(140px,1.3fr)_104px_76px_84px_56px_76px_92px_84px_80px] items-center gap-2 border-t border-line/40 py-1 text-[11px]", closed && "opacity-55")}>
              <span className={clsx("grid h-4 w-4 place-items-center rounded-[4px] text-[9px] font-bold", l.side === "buy" ? "bg-pos/15 text-pos" : "bg-neg/15 text-neg")}>
                {l.side === "buy" ? "B" : "S"}
              </span>
              <span className="flex min-w-0 items-center gap-1.5 text-[11px] font-medium">
                <span
                  className="inline-block h-[3px] w-3.5 flex-none rounded-full"
                  style={{ background: legColors[l.id] }}
                  title="chart line color"
                />
                <span className="tnum truncate">{l.symbol}</span>
                {closed && (
                  <span className="rounded-[3px] bg-surface-3 px-1 text-[8px] uppercase text-text-mute" title={l.exitAt ? `closed ${fmtEntry(l.exitAt)} · ${l.exitReason}` : ""}>
                    exit · {l.exitReason}
                  </span>
                )}
              </span>
              <span className="tnum text-[10px] text-text-mute">{closed && l.exitAt ? fmtEntry(l.exitAt) : fmtEntry(p.openedAt)}</span>
              <span className={clsx("tnum text-right", sizeBtc < 0 ? "text-neg" : "text-pos")}>{sizeBtc.toFixed(3)}</span>
              <span className="tnum text-right text-text-dim">{money(notional, currency)}</span>
              <span className="tnum text-right text-text-dim">{l.entry.toFixed(1)}</span>
              <span className="tnum text-right text-text-mute">{index.toLocaleString("en-US", { maximumFractionDigits: 1 })}</span>
              <span className="tnum text-right">
                {closed ? (
                  <span title="exit fill price">exit {l.exitPrice?.toFixed(1)}</span>
                ) : (
                  <>{mark.toFixed(1)} <span className="text-[9px] text-text-mute">{(iv * 100).toFixed(1)}%</span></>
                )}
              </span>
              <span className={clsx("tnum text-right font-medium", lpnl >= 0 ? "text-pos" : "text-neg")} title={closed ? `realized: gross ${money(l.exitGross ?? 0, currency)} − fees ${money(l.exitFees ?? 0, currency)}` : "live UPNL"}>
                {lpnl >= 0 ? "+" : ""}{money(lpnl, currency)}
              </span>
              <span className="tnum text-right text-text-dim">{money(cashflow, currency)}</span>
            </div>
          );
        })}
      </div>

      {/* Risk & exit (SL/TP) — above the analytics charts */}
      {!closed && live && <LiqPanel check={live.check} currency={currency} />}
      {!closed && <RiskPanel p={p} currency={currency} live={!!live} />}

      {/* Position analytics: stacked MTM / IV / Δ / Θ / Vega. Open positions show them
          on expand; CLOSED positions show them behind the "Analyse" button (entry→close).
          Series is lazy-loaded on open — placeholder until the history arrives. */}
      {(!closed || analyse) && (
        <div className="border-t border-line/60 px-3 py-2">
          {p.series.length === 0 ? (
            <div className="grid h-24 place-items-center text-[11px] text-text-mute">
              <div className="flex items-center gap-2">
                <RefreshCw size={12} className="animate-spin" /> loading chart…
              </div>
            </div>
          ) : (
            <PositionCharts series={p.series} legs={p.legs} currency={currency} />
          )}
        </div>
      )}
        </>
      )}
    </div>
  );
}

// Risk & exit controls for a placed strategy: combined net-capital SL (₹/$ or
// % of margin) + per-leg TP/SL, close-scope, auto-exit, and a per-leg Close.
function RiskPanel({ p, currency, live }: { p: Position; currency: Currency; live: boolean }) {
  const closeLeg = useStore((s) => s.closeLeg);
  const setStop = useStore((s) => s.setPositionStop);
  const setLegRisk = useStore((s) => s.setPositionLegRisk);
  const [slMode, setSlMode] = useState<"amount" | "pct">(p.stopLossPctOfMargin != null ? "pct" : "amount");
  const floor = combinedFloor({ lossAmount: p.stopLossAmount, lossPctOfMargin: p.stopLossPctOfMargin }, p.margin);
  // amounts are stored in USD; type/show them in the selected currency (fixed 85, as Delta)
  const rate = currency === "INR" ? USDINR : 1;
  const cur = currency === "INR" ? "₹" : "$";
  const shown = (usd: number | null) => (usd == null ? null : +(usd * rate).toFixed(2));
  const usd = (n: number | null) => (n == null ? null : n / rate);

  return (
    <div className="space-y-2 border-t border-line/60 px-4 py-2.5">
      {/* combined net-capital stop */}
      <div className="flex flex-wrap items-center gap-2.5 text-[11px]">
        <span className="text-[9px] uppercase tracking-wider text-text-mute">{live ? "Basket SL (exit all)" : "Combined SL"}</span>
        <Seg2
          value={slMode}
          onChange={setSlMode}
          opts={[{ v: "amount", label: currency === "INR" ? "₹" : "$" }, { v: "pct", label: "% margin" }]}
        />
        {slMode === "amount" ? (
          <NumInput key={currency} value={shown(p.stopLossAmount)} placeholder={`loss ${cur}`} onChange={(n) => setStop(p.id, { stopLossAmount: usd(n), stopLossPctOfMargin: null })} />
        ) : (
          <NumInput value={p.stopLossPctOfMargin} placeholder="% margin" onChange={(n) => setStop(p.id, { stopLossPctOfMargin: n, stopLossAmount: null })} />
        )}
        {floor != null && <span className="tnum text-[10px] text-text-mute">floor {money(floor, currency)}</span>}
        {live && (
          <span className="text-[10px] text-text-mute">
            Any leg SL or the basket SL closes <b className="text-text-dim">every</b> leg (reduce-only). Arm to activate.
          </span>
        )}
        {!live && (<>
        <label className="flex cursor-pointer items-center gap-1 text-text-mute">
          <input type="checkbox" checked={p.autoExit} onChange={(e) => setStop(p.id, { autoExit: e.target.checked })} className="accent-accent" />
          Auto-exit
        </label>
        <label
          className="flex cursor-pointer items-center gap-1 text-text-mute"
          title="Keep firing LOSS stops on the last-known marks during a stale feed (after a 30s grace) instead of pausing. Stops only, never take-profit. Fires-on-recovery automatically."
        >
          <input type="checkbox" checked={p.staleHardStop} onChange={(e) => setStop(p.id, { staleHardStop: e.target.checked })} className="accent-warn" />
          Stale hard-stop
        </label>
        </>)}
      </div>

      {/* per-leg TP/SL + close (open legs only) */}
      {p.legs.filter((l) => l.status === "open").map((l) => (
        <div key={l.id} className="flex flex-wrap items-center gap-2 text-[11px]">
          <span className="w-16 font-medium">{l.strike} {l.type === "call" ? "CE" : "PE"}</span>
          {live ? (
            <>
              <span className="text-[9px] uppercase text-text-mute">SL loss</span>
              <NumInput key={currency} value={shown(l.stopPnl == null ? null : Math.abs(l.stopPnl))} placeholder={cur} onChange={(n) => setLegRisk(p.id, l.id, { stopPnl: n == null ? null : -Math.abs(usd(n) as number) })} />
              <span className="tnum text-[10px] text-text-mute" title="Reduce-only stop-market resting on Delta (mark-price trigger). Fires even if this server is down.">
                {l.stopPrice != null ? <>Delta stop @ <span className="text-text-dim">{l.stopPrice}</span></> : p.autoExit ? <span className="text-warn">no stop on Delta</span> : "no stop (disarmed)"}
              </span>
            </>
          ) : (<>
          <span className="text-[9px] uppercase text-text-mute">TP</span>
          <NumInput key={currency} value={shown(l.targetPnl)} placeholder={cur} onChange={(n) => setLegRisk(p.id, l.id, { targetPnl: usd(n) })} />
          <span className="text-[9px] uppercase text-text-mute">SL</span>
          <NumInput key={currency} value={shown(l.stopPnl)} placeholder={cur} onChange={(n) => setLegRisk(p.id, l.id, { stopPnl: usd(n) })} />
          <Seg2
            value={l.closeScope}
            onChange={(v) => setLegRisk(p.id, l.id, { closeScope: v })}
            opts={[{ v: "leg", label: "leg" }, { v: "strategy", label: "strat" }]}
          />
          <label className="flex cursor-pointer items-center gap-1 text-text-mute">
            <input type="checkbox" checked={l.autoExit} onChange={(e) => setLegRisk(p.id, l.id, { autoExit: e.target.checked })} className="accent-accent" />
            Auto
          </label>
          <button
            onClick={() => closeLeg(p.id, l.id)}
            className="ml-auto flex items-center gap-1 rounded-[4px] border border-neg/40 px-1.5 py-0.5 text-[10px] font-semibold text-neg hover:bg-neg/10"
          >
            <X size={10} /> Close leg
          </button>
          </>)}
        </div>
      ))}
    </div>
  );
}

// number input that maps empty → null (for optional TP/SL levels). Commits on Enter/blur
// only: per-keystroke saves would race each other and briefly arm half-typed stops (typing
// "8500" would pass through 8 and 85 — a live SL that could fire mid-typing).
function NumInput({ value, onChange, placeholder }: { value: number | null; onChange: (n: number | null) => void; placeholder?: string }) {
  const [v, setV] = useState(value == null ? "" : String(value));
  const commit = () => {
    const n = v === "" || v === "-" || v === "." ? null : Number(v);
    const next = n != null && Number.isFinite(n) ? n : null;
    if (next !== value) onChange(next);
  };
  return (
    <input
      type="text"
      inputMode="decimal"
      value={v}
      placeholder={placeholder}
      onChange={(e) => setV(e.target.value.replace(/[^0-9.-]/g, ""))}
      onBlur={commit}
      onKeyDown={(e) => { if (e.key === "Enter") e.currentTarget.blur(); }}
      className="tnum w-16 rounded bg-surface-2 px-1.5 py-0.5 text-[11px] text-text outline-none placeholder:text-text-mute focus:ring-1 focus:ring-accent"
    />
  );
}

function Seg2<T extends string>({ value, onChange, opts }: { value: T; onChange: (v: T) => void; opts: { v: T; label: string }[] }) {
  return (
    <div className="flex overflow-hidden rounded-[4px] border border-line">
      {opts.map((o) => (
        <button
          key={o.v}
          onClick={() => onChange(o.v)}
          className={clsx("px-1.5 py-0.5 text-[10px]", value === o.v ? "bg-surface-3 text-text" : "text-text-mute hover:text-text-dim")}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}


// Arm/disarm (server runs the SL-vs-liquidation check first) + manual "exit all now".
function LiveControls({ p, live }: { p: Position; live: LiveProps }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function arm(armed: boolean) {
    setBusy(true);
    setErr(null);
    const r = await armLiveGroup(p.id, armed);
    if (!r.ok) setErr(r.error);
    live.onChanged();
    setBusy(false);
  }

  async function exitAll() {
    if (!window.confirm(`Close ALL legs of ${p.name} on Delta now (reduce-only)?`)) return;
    setBusy(true);
    setErr(null);
    const r = await exitLiveGroup(p.id);
    if (!r.ok) setErr(r.error);
    live.onChanged();
    setBusy(false);
  }

  return (
    <>
      {err && <span className="max-w-[320px] text-[10px] text-neg">{err}</span>}
      <button
        onClick={() => arm(!p.autoExit)}
        disabled={busy || p.exiting}
        className={clsx(
          "flex items-center gap-1 rounded-[5px] border px-2.5 py-1 text-[11px] font-semibold disabled:opacity-50",
          p.autoExit ? "border-pos/50 text-pos hover:bg-pos/10" : "border-line text-text-dim hover:text-text",
        )}
        title={p.autoExit ? "Disarm: cancels the Delta stops; tracking continues" : "Arm SL: checks the SL comes before liquidation, then places a reduce-only stop on Delta per leg"}
      >
        <Power size={12} /> {p.autoExit ? "Armed" : "Arm SL"}
      </button>
      <button
        onClick={exitAll}
        disabled={busy || !live.tradingEnabled}
        className="flex items-center gap-1 rounded-[5px] border border-neg/50 bg-neg/10 px-2.5 py-1 text-[11px] font-bold text-neg hover:bg-neg/20 disabled:opacity-50"
        title={live.tradingEnabled ? "Reduce-only close of every leg, shorts first" : "Live trading is switched off on the server (LIVE_TRADING_ENABLED)"}
      >
        <X size={12} /> Exit all now
      </button>
    </>
  );
}

// Does the SL fire before Delta would liquidate the account?
function LiqPanel({ check, currency }: { check?: LiveCheck; currency: Currency }) {
  if (!check) {
    return (
      <div className="border-t border-line/60 px-4 py-2 text-[11px] text-text-mute">
        Liquidation check: waiting for the first wallet read…
      </div>
    );
  }
  const tone = check.verdict === "green" ? "text-pos" : check.verdict === "yellow" ? "text-warn" : "text-neg";
  const label =
    check.verdict === "green"
      ? "SL fires well before liquidation"
      : check.verdict === "yellow"
        ? "SL is close to liquidation"
        : "Liquidation can come BEFORE the SL";
  const spot = (v: number | null) => (v == null ? "beyond ±50%" : v.toLocaleString("en-US", { maximumFractionDigits: 0 }));
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-line/60 px-4 py-2 text-[11px]">
      <span className={clsx("flex items-center gap-1 font-semibold", tone)}>
        <ShieldAlert size={12} /> {label}
      </span>
      <span className="text-text-mute">SL spot <span className="tnum text-text-dim">{spot(check.sl_spot)}</span></span>
      <span className="text-text-mute">Liquidation spot <span className="tnum text-text-dim">{spot(check.liq_spot)}</span></span>
      <span className="text-text-mute">Equity <span className="tnum text-text-dim">{money(check.equity, currency)}</span></span>
      <span className="text-text-mute">Maint. margin <span className="tnum text-text-dim">{money(check.maintenance_margin, currency)}</span></span>
      {check.usage != null && (
        <span className="text-text-mute">
          Usage{" "}
          <span className={clsx("tnum", check.usage >= 0.8 ? "text-neg" : check.usage >= 0.6 ? "text-warn" : "text-text-dim")}>
            {(check.usage * 100).toFixed(0)}%
          </span>
        </span>
      )}
      <span className="text-text-mute" title="Largest multiple of this group's current size that still keeps the SL firing with equity ≥ 1.25× maintenance margin">
        Safe size <span className="tnum text-text-dim">≤ {check.max_scale.toFixed(2)}× current</span>
      </span>
      <span className="text-[10px] text-text-mute">{check.reason}</span>
    </div>
  );
}
