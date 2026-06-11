"use client";

import { useState } from "react";
import clsx from "clsx";
import { ChevronDown, ChevronRight, Download, LineChart, X } from "lucide-react";
import PayoffPanel from "@/components/PayoffPanel";
import PositionCharts from "@/components/PositionCharts";
import { exportPositionXlsx } from "@/lib/exportXlsx";
import { legPnl } from "@/lib/engine";
import { combinedFloor } from "@/lib/exit";
import { legColorMap } from "@/lib/legColors";
import { ACTIVE_FEE, type Currency, entryFee, entrySlippage, legIv, legMark, money, positionPnl, spotOf, useStore } from "@/lib/store";
import type { Position } from "@/lib/types";

// entry date + time, e.g. "09 Jun 14:36" (legs are placed at position open)
function fmtEntry(ms: number): string {
  const d = new Date(ms);
  return `${d.toLocaleDateString("en-GB", { day: "2-digit", month: "short" })} ${d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })}`;
}

export default function PositionsPage() {
  // subscribe to positions + tickN so live marks re-render the PnL
  const positions = useStore((s) => s.positions);
  const currency = useStore((s) => s.currency);
  useStore((s) => s.tickN);
  const closePosition = useStore((s) => s.closePosition);

  const open = positions.filter((p) => p.status === "open");
  const totalUpnl = open.reduce((s, p) => s + positionPnl(p), 0);
  const totalMargin = open.reduce((s, p) => s + p.margin, 0);

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-line px-4 py-2.5">
        <h1 className="text-[13px] font-semibold tracking-tight text-text">Positions</h1>
        <span className="text-[11px] text-text-mute">{open.length} open</span>
        {open.length > 0 && (
          <div className="ml-auto flex items-center gap-5 text-[11px]">
            <span className="text-text-mute">
              Margin used <span className="tnum text-text-dim">{money(totalMargin, currency)}</span>
            </span>
            <span className="text-text-mute">
              Total UPNL{" "}
              <span className={clsx("tnum font-semibold", totalUpnl >= 0 ? "text-pos" : "text-neg")}>
                {totalUpnl >= 0 ? "+" : ""}{money(totalUpnl, currency)}
              </span>
            </span>
          </div>
        )}
      </div>

      {positions.length === 0 ? (
        <div className="grid flex-1 place-items-center text-center text-[13px] text-text-mute">
          <div>
            No positions yet.
            <div className="mt-1 text-[11px]">Build a strategy on the chain and Place Paper Order.</div>
          </div>
        </div>
      ) : (
        <div className="min-h-0 flex-1 space-y-3 overflow-auto p-4">
          {positions.map((p) => (
            <PositionCard key={p.id} p={p} currency={currency} onClose={closePosition} />
          ))}
        </div>
      )}
    </div>
  );
}

function PositionCard({
  p, currency, onClose,
}: { p: Position; currency: Currency; onClose: (id: string) => void }) {
  const pnl = positionPnl(p);
  const closed = p.status === "closed";
  const exp = p.expiry.slice(5);
  // each strategy collapses; closed strategies start collapsed
  const [open, setOpen] = useState(!closed);
  const [analyse, setAnalyse] = useState(false);
  // per-leg chart colors → swatch in the Symbol column maps rows to chart lines
  const legColors = legColorMap(p.legs);

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
        {closed && (
          <span className="text-[10px] text-text-mute">
            <span className="uppercase tracking-wider">closed</span>
            {p.closeReason && <span className="ml-1 text-text-dim">· {p.closeReason}</span>}
            {p.closedAt && <span className="ml-1 tnum">· {fmtEntry(p.closedAt)}</span>}
          </span>
        )}
        <div className="ml-auto flex items-center gap-5 text-[11px]">
          <span className="text-text-mute">
            Margin <span className="tnum text-text-dim">{money(p.margin, currency)}</span>
          </span>
          <span className="text-text-mute" title="cost of crossing the spread on entry (already in MTM)">
            Entry slip <span className="tnum text-warn">{money(entrySlippage(p), currency)}</span>
          </span>
          <span className="text-text-mute" title={`Delta options fee (${ACTIVE_FEE.name}): min(${(ACTIVE_FEE.notionalRate * 100).toFixed(3)}% notional, ${(ACTIVE_FEE.premiumCap * 100).toFixed(1)}% premium) +18% GST. Charged to realized PnL on close, not to MTM.`}>
            Entry fee <span className="tnum text-text-dim">{money(entryFee(p), currency)}</span>
            <span className="ml-1 text-[9px] text-accent">{ACTIVE_FEE.name}</span>
          </span>
          <span className="text-text-mute">
            UPNL{" "}
            <span className={clsx("tnum text-[13px] font-semibold", pnl >= 0 ? "text-pos" : "text-neg")}>
              {pnl >= 0 ? "+" : ""}{money(pnl, currency)}
            </span>
          </span>
          <button
            onClick={() => { setOpen(true); setAnalyse((a) => !a); }}
            className={clsx("flex items-center gap-1 rounded-[5px] border px-2 py-1 text-[11px]", analyse ? "border-accent text-accent" : "border-line text-text-mute hover:text-text")}
            title="Analyse this strategy's payoff (what-if spot / date / IV)"
          >
            <LineChart size={12} /> Analyse
          </button>
          <button
            onClick={() => exportPositionXlsx(p)}
            className="flex items-center gap-1 rounded-[5px] border border-line px-2 py-1 text-[11px] text-text-mute hover:text-text"
            title="Download full strategy + every panel's chart data as Excel (.xlsx)"
          >
            <Download size={12} /> Excel
          </button>
          {!closed && (
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
      {analyse && (
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
      {!closed && <RiskPanel p={p} currency={currency} />}

      {/* Position analytics: stacked MTM / IV / Δ / Θ / Vega, shared timeframe */}
      <div className="border-t border-line/60 px-3 py-2">
        <PositionCharts series={p.series} legs={p.legs} currency={currency} />
      </div>
        </>
      )}
    </div>
  );
}

// Risk & exit controls for a placed strategy: combined net-capital SL (₹/$ or
// % of margin) + per-leg TP/SL, close-scope, auto-exit, and a per-leg Close.
function RiskPanel({ p, currency }: { p: Position; currency: Currency }) {
  const closeLeg = useStore((s) => s.closeLeg);
  const setStop = useStore((s) => s.setPositionStop);
  const setLegRisk = useStore((s) => s.setPositionLegRisk);
  const [slMode, setSlMode] = useState<"amount" | "pct">(p.stopLossPctOfMargin != null ? "pct" : "amount");
  const floor = combinedFloor({ lossAmount: p.stopLossAmount, lossPctOfMargin: p.stopLossPctOfMargin }, p.margin);

  return (
    <div className="space-y-2 border-t border-line/60 px-4 py-2.5">
      {/* combined net-capital stop */}
      <div className="flex flex-wrap items-center gap-2.5 text-[11px]">
        <span className="text-[9px] uppercase tracking-wider text-text-mute">Combined SL</span>
        <Seg2
          value={slMode}
          onChange={setSlMode}
          opts={[{ v: "amount", label: currency === "INR" ? "₹" : "$" }, { v: "pct", label: "% margin" }]}
        />
        {slMode === "amount" ? (
          <NumInput value={p.stopLossAmount} placeholder="loss" onChange={(n) => setStop(p.id, { stopLossAmount: n, stopLossPctOfMargin: null })} />
        ) : (
          <NumInput value={p.stopLossPctOfMargin} placeholder="% margin" onChange={(n) => setStop(p.id, { stopLossPctOfMargin: n, stopLossAmount: null })} />
        )}
        {floor != null && <span className="tnum text-[10px] text-text-mute">floor {money(floor, currency)}</span>}
        <label className="flex cursor-pointer items-center gap-1 text-text-mute">
          <input type="checkbox" checked={p.autoExit} onChange={(e) => setStop(p.id, { autoExit: e.target.checked })} className="accent-accent" />
          Auto-exit
        </label>
      </div>

      {/* per-leg TP/SL + close (open legs only) */}
      {p.legs.filter((l) => l.status === "open").map((l) => (
        <div key={l.id} className="flex flex-wrap items-center gap-2 text-[11px]">
          <span className="w-16 font-medium">{l.strike} {l.type === "call" ? "CE" : "PE"}</span>
          <span className="text-[9px] uppercase text-text-mute">TP</span>
          <NumInput value={l.targetPnl} placeholder="$" onChange={(n) => setLegRisk(p.id, l.id, { targetPnl: n })} />
          <span className="text-[9px] uppercase text-text-mute">SL</span>
          <NumInput value={l.stopPnl} placeholder="$" onChange={(n) => setLegRisk(p.id, l.id, { stopPnl: n })} />
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
        </div>
      ))}
    </div>
  );
}

// number input that maps empty → null (for optional TP/SL levels)
function NumInput({ value, onChange, placeholder }: { value: number | null; onChange: (n: number | null) => void; placeholder?: string }) {
  const [v, setV] = useState(value == null ? "" : String(value));
  return (
    <input
      type="text"
      inputMode="decimal"
      value={v}
      placeholder={placeholder}
      onChange={(e) => {
        const raw = e.target.value.replace(/[^0-9.-]/g, "");
        setV(raw);
        const n = raw === "" || raw === "-" || raw === "." ? null : Number(raw);
        onChange(n != null && Number.isFinite(n) ? n : null);
      }}
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

