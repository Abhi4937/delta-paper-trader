"use client";

import { useState } from "react";
import clsx from "clsx";
import { ChevronDown, ChevronRight, X } from "lucide-react";
import PositionCharts from "@/components/PositionCharts";
import { legPnl } from "@/lib/engine";
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
      <div className="flex items-center gap-4 border-b border-line px-4 py-2.5">
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
  // per-leg chart colors → swatch in the Symbol column maps rows to chart lines
  const legColors = legColorMap(p.legs);

  return (
    <div className={clsx("rounded-lg border bg-surface", closed ? "border-line/60 opacity-60" : "border-line")}>
      {/* header */}
      <div className={clsx("flex items-center gap-3 px-4 py-2.5", open && "border-b border-line/60")}>
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
        {closed && <span className="text-[10px] uppercase tracking-wider text-text-mute">closed</span>}
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
      {/* legs — Delta-style columns (compact) */}
      <div className="overflow-x-auto px-4 py-0.5">
        <div className="grid min-w-[880px] grid-cols-[28px_minmax(140px,1.3fr)_104px_76px_84px_56px_76px_92px_84px_80px] gap-2 py-1 text-[9px] uppercase tracking-wider text-text-mute">
          <span>B/S</span><span>Symbol</span><span>Entry Time</span><span className="text-right">Size BTC</span>
          <span className="text-right">Notional</span><span className="text-right">Entry</span>
          <span className="text-right">Index</span><span className="text-right">Mark · IV</span>
          <span className="text-right">UPNL</span><span className="text-right">Cashflow</span>
        </div>
        {p.legs.map((l) => {
          const mark = legMark(l);
          const iv = legIv(l);
          const lpnl = legPnl(l, mark);
          const sign = l.side === "sell" ? -1 : 1;
          const sizeBtc = sign * l.qty * l.contractValue;
          const index = spotOf(l.underlying) || l.spotAtEntry;
          const notional = l.qty * l.contractValue * index;
          const cashflow = (l.side === "sell" ? 1 : -1) * l.entry * l.qty * l.contractValue;
          return (
            <div key={l.id} className="grid min-w-[880px] grid-cols-[28px_minmax(140px,1.3fr)_104px_76px_84px_56px_76px_92px_84px_80px] items-center gap-2 border-t border-line/40 py-1 text-[11px]">
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
              </span>
              <span className="tnum text-[10px] text-text-mute">{fmtEntry(p.openedAt)}</span>
              <span className={clsx("tnum text-right", sizeBtc < 0 ? "text-neg" : "text-pos")}>{sizeBtc.toFixed(3)}</span>
              <span className="tnum text-right text-text-dim">{money(notional, currency)}</span>
              <span className="tnum text-right text-text-dim">{l.entry.toFixed(1)}</span>
              <span className="tnum text-right text-text-mute">{index.toLocaleString("en-US", { maximumFractionDigits: 1 })}</span>
              <span className="tnum text-right">
                {mark.toFixed(1)} <span className="text-[9px] text-text-mute">{(iv * 100).toFixed(1)}%</span>
              </span>
              <span className={clsx("tnum text-right font-medium", lpnl >= 0 ? "text-pos" : "text-neg")}>
                {lpnl >= 0 ? "+" : ""}{money(lpnl, currency)}
              </span>
              <span className="tnum text-right text-text-dim">{money(cashflow, currency)}</span>
            </div>
          );
        })}
      </div>

      {/* Position analytics: stacked MTM / IV / Δ / Θ / Vega, shared timeframe */}
      <div className="border-t border-line/60 px-3 py-2">
        <PositionCharts series={p.series} legs={p.legs} currency={currency} />
      </div>

      {/* footer: target / stop levels */}
      {(p.targetPnl != null || p.stopPnl != null) && (
        <div className="flex items-center gap-4 border-t border-line/60 px-4 py-2">
          {p.targetPnl != null && (
            <span className="text-[10px] text-text-mute">TP <span className="tnum text-pos">{money(p.targetPnl, currency)}</span></span>
          )}
          {p.stopPnl != null && (
            <span className="text-[10px] text-text-mute">SL <span className="tnum text-neg">{money(p.stopPnl, currency)}</span></span>
          )}
        </div>
      )}
        </>
      )}
    </div>
  );
}

