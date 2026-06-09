"use client";

import clsx from "clsx";
import { X } from "lucide-react";
import { legPnl } from "@/lib/engine";
import { ACTIVE_FEE, type Currency, entryFee, entrySlippage, legIv, legMark, money, positionPnl, spotOf, useStore } from "@/lib/store";
import type { Leg, Position } from "@/lib/types";

function legLabel(l: Leg): string {
  return `${l.strike} ${l.type === "call" ? "CE" : "PE"}`;
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

  return (
    <div className={clsx("rounded-lg border bg-surface", closed ? "border-line/60 opacity-60" : "border-line")}>
      {/* header */}
      <div className="flex items-center gap-3 border-b border-line/60 px-4 py-2.5">
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
        </div>
      </div>

      {/* legs — Delta-style columns */}
      <div className="overflow-x-auto px-4 py-1">
        <div className="grid min-w-[760px] grid-cols-[28px_minmax(150px,1.4fr)_82px_92px_64px_84px_96px_88px_84px] gap-2 py-1 text-[9px] uppercase tracking-wider text-text-mute">
          <span>B/S</span><span>Symbol</span><span className="text-right">Size BTC</span>
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
            <div key={l.id} className="grid min-w-[760px] grid-cols-[28px_minmax(150px,1.4fr)_82px_92px_64px_84px_96px_88px_84px] items-center gap-2 border-t border-line/40 py-1.5 text-[12px]">
              <span className={clsx("grid h-5 w-5 place-items-center rounded-[4px] text-[10px] font-bold", l.side === "buy" ? "bg-pos/15 text-pos" : "bg-neg/15 text-neg")}>
                {l.side === "buy" ? "B" : "S"}
              </span>
              <span className="tnum truncate text-[11px] font-medium">{l.symbol}</span>
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

      {/* MTM chart: 0-line, green above / red below, auto-scaled */}
      <div className="border-t border-line/60 px-3 pt-2">
        <div className="mb-1 flex items-center justify-between text-[9px] uppercase tracking-wider text-text-mute">
          <span>MTM (entry slippage only · before fees)</span>
          <span className="tnum normal-case">
            {elapsed(p.mtm[p.mtm.length - 1].t - p.mtm[0].t)} · {p.mtm.length} pts
          </span>
        </div>
        <MtmChart mtm={p.mtm} id={p.id} />
      </div>

      {/* footer actions */}
      <div className="flex items-center gap-4 px-4 py-2">
        {p.targetPnl != null && (
          <span className="text-[10px] text-text-mute">TP <span className="tnum text-pos">{money(p.targetPnl, currency)}</span></span>
        )}
        {p.stopPnl != null && (
          <span className="text-[10px] text-text-mute">SL <span className="tnum text-neg">{money(p.stopPnl, currency)}</span></span>
        )}
        <div className="ml-auto">
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
    </div>
  );
}

// MTM chart: a real per-second time series. Fixed start point (0 at open) on the
// left, points appended every second; x = elapsed time, green above 0 / red below.
function MtmChart({ mtm, id }: { mtm: { t: number; pnl: number }[]; id: string }) {
  const W = 600;
  const H = 80;
  const t0 = mtm[0].t;
  const tLast = mtm[mtm.length - 1].t;
  const span = Math.max(tLast - t0, 1);
  const ys = mtm.map((m) => m.pnl);
  const min = Math.min(...ys, 0);
  const max = Math.max(...ys, 0);
  const pad = (max - min) * 0.15 || 1;
  const lo = min - pad;
  const hi = max + pad;
  const range = hi - lo;
  const x = (t: number) => ((t - t0) / span) * W;
  const y = (v: number) => H - ((v - lo) / range) * H;
  const zeroY = y(0);
  const cur = mtm[mtm.length - 1];
  const line = mtm.map((m) => `${x(m.t)},${y(m.pnl)}`).join(" ");
  const area = `M${x(t0)},${zeroY} ${mtm.map((m) => `L${x(m.t)},${y(m.pnl)}`).join(" ")} L${x(tLast)},${zeroY} Z`;
  const sid = id.replace(/[^a-zA-Z0-9]/g, "");
  const gid = `g${sid}`;
  const rid = `r${sid}`;
  const up = cur.pnl >= 0;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" width="100%" height={H}>
      <defs>
        <clipPath id={gid}><rect x="0" y="0" width={W} height={Math.max(0, zeroY)} /></clipPath>
        <clipPath id={rid}><rect x="0" y={zeroY} width={W} height={Math.max(0, H - zeroY)} /></clipPath>
      </defs>
      {mtm.length > 1 && (
        <>
          <path d={area} className="fill-pos/15" clipPath={`url(#${gid})`} />
          <path d={area} className="fill-neg/15" clipPath={`url(#${rid})`} />
        </>
      )}
      <line x1="0" y1={zeroY} x2={W} y2={zeroY} className="stroke-text-mute" strokeWidth="1" strokeDasharray="4 4" vectorEffect="non-scaling-stroke" />
      <polyline points={line} fill="none" className="stroke-pos" strokeWidth="1.5" clipPath={`url(#${gid})`} vectorEffect="non-scaling-stroke" />
      <polyline points={line} fill="none" className="stroke-neg" strokeWidth="1.5" clipPath={`url(#${rid})`} vectorEffect="non-scaling-stroke" />
      {/* start point (always at 0 on the left) */}
      <circle cx={x(t0)} cy={zeroY} r="3.5" className="fill-text-mute" vectorEffect="non-scaling-stroke" />
      {/* current point, walks right as seconds are added */}
      <circle cx={x(cur.t)} cy={y(cur.pnl)} r="4" className={up ? "fill-pos" : "fill-neg"} vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

function elapsed(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  const m = Math.floor(s / 60);
  return m > 0 ? `${m}m ${s % 60}s` : `${s}s`;
}
