"use client";

import clsx from "clsx";
import { money, positionPnl, useStore } from "@/lib/store";
import type { Position } from "@/lib/types";

const fmtT = (t: number) =>
  new Date(t).toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });

// realized = sum over closed legs of (gross − fees); fees = sum of closed-leg fees
const realizedOf = (p: Position) => p.legs.reduce((s, l) => (l.status === "closed" ? s + (l.exitGross ?? 0) - (l.exitFees ?? 0) : s), 0);
const feesOf = (p: Position) => p.legs.reduce((s, l) => s + (l.status === "closed" ? l.exitFees ?? 0 : 0), 0);

export default function AnalyticsPage() {
  const positions = useStore((s) => s.positions);
  const balance = useStore((s) => s.balance);
  const currency = useStore((s) => s.currency);
  useStore((s) => s.tickN); // live UPNL/greeks

  const open = positions.filter((p) => p.status === "open");
  const closed = positions.filter((p) => p.status === "closed");

  const openUpnl = open.reduce((s, p) => s + positionPnl(p), 0);
  const openMargin = open.reduce((s, p) => s + p.margin, 0);
  const realizedTotal = positions.reduce((s, p) => s + realizedOf(p), 0);
  const feesTotal = positions.reduce((s, p) => s + feesOf(p), 0);

  const trades = closed.map((p) => ({ p, net: realizedOf(p), at: p.closedAt ?? p.openedAt })).sort((a, b) => a.at - b.at);
  const wins = trades.filter((t) => t.net > 0).length;
  const winRate = trades.length ? (wins / trades.length) * 100 : 0;
  const best = trades.length ? Math.max(...trades.map((t) => t.net)) : 0;
  const worst = trades.length ? Math.min(...trades.map((t) => t.net)) : 0;

  // net OPEN greeks from each position's latest sample
  const g = open.reduce((a, p) => {
    const last = p.series[p.series.length - 1];
    if (last) { a.delta += last.delta; a.theta += last.theta; a.vega += last.vega; }
    return a;
  }, { delta: 0, theta: 0, vega: 0 });

  // cumulative realized over time (functional running total)
  const cum = trades.map((t, i) => ({ at: t.at, v: trades.slice(0, i + 1).reduce((s, x) => s + x.net, 0) }));

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-3 border-b border-line px-4 py-2.5">
        <h1 className="text-[13px] font-semibold tracking-tight text-text">Analytics</h1>
        <span className="text-[11px] text-text-mute">{open.length} open · {closed.length} closed</span>
      </div>

      <div className="min-h-0 flex-1 space-y-4 overflow-auto p-4">
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
          <Card label="Balance" value={money(balance, currency)} />
          <Card label="Open UPNL" value={money(openUpnl, currency)} tone={openUpnl >= 0 ? "pos" : "neg"} />
          <Card label="Realized (net)" value={money(realizedTotal, currency)} tone={realizedTotal >= 0 ? "pos" : "neg"} />
          <Card label="Fees paid" value={money(feesTotal, currency)} tone="neg" />
          <Card label="Win rate" value={trades.length ? `${winRate.toFixed(0)}%` : "—"} sub={trades.length ? `${wins}/${trades.length}` : ""} />
          <Card label="Margin in use" value={money(openMargin, currency)} />
        </div>

        <div className="grid gap-4 lg:grid-cols-[1.4fr_1fr]">
          {/* cumulative realized */}
          <div className="rounded-lg border border-line bg-surface-2 p-3">
            <div className="mb-2 flex items-center justify-between">
              <span className="text-[11px] font-medium text-text-dim">Cumulative realized P&amp;L</span>
              <span className="text-[10px] text-text-mute">best {money(best, currency)} · worst {money(worst, currency)}</span>
            </div>
            <CumChart points={cum} />
          </div>

          {/* open exposure */}
          <div className="rounded-lg border border-line bg-surface-2 p-3">
            <div className="mb-2 text-[11px] font-medium text-text-dim">Open exposure (net greeks)</div>
            <div className="grid grid-cols-3 gap-2">
              <Card label="Net Δ" value={g.delta.toFixed(3)} small />
              <Card label="Net Θ /day" value={money(g.theta, currency)} tone={g.theta >= 0 ? "pos" : "neg"} small />
              <Card label="Net Vega" value={money(g.vega, currency)} small />
            </div>
            <p className="mt-2 text-[10px] text-text-mute">Σ across {open.length} open {open.length === 1 ? "strategy" : "strategies"} (latest marks). Θ = daily decay, Vega = per +1 vol-pt.</p>
          </div>
        </div>

        {/* closed trades */}
        <div className="overflow-x-auto rounded-lg border border-line bg-surface-2">
          <div className="grid min-w-[480px] grid-cols-[1.6fr_1fr_1fr_1fr_1fr] gap-2 border-b border-line px-3 py-1.5 text-[9px] uppercase tracking-wider text-text-mute">
            <span>Strategy</span><span>Closed</span><span className="text-right">Gross</span><span className="text-right">Fees</span><span className="text-right">Net</span>
          </div>
          {trades.length === 0 ? (
            <div className="px-3 py-6 text-center text-[12px] text-text-mute">No closed trades yet.</div>
          ) : (
            [...trades].reverse().map(({ p, net }) => {
              const gross = p.legs.reduce((s, l) => s + (l.status === "closed" ? l.exitGross ?? 0 : 0), 0);
              return (
                <div key={p.id} className="grid min-w-[480px] grid-cols-[1.6fr_1fr_1fr_1fr_1fr] items-center gap-2 border-t border-line/40 px-3 py-1.5 text-[12px]">
                  <span className="truncate text-text-dim">{p.name} <span className="text-[10px] text-text-mute">{p.closeReason ?? ""}</span></span>
                  <span className="tnum text-[11px] text-text-mute">{p.closedAt ? fmtT(p.closedAt) : "—"}</span>
                  <span className="tnum text-right text-text-dim">{money(gross, currency)}</span>
                  <span className="tnum text-right text-neg">{money(feesOf(p), currency)}</span>
                  <span className={clsx("tnum text-right font-medium", net >= 0 ? "text-pos" : "text-neg")}>{money(net, currency)}</span>
                </div>
              );
            })
          )}
        </div>
      </div>
    </div>
  );
}

function Card({ label, value, tone, sub, small }: { label: string; value: string; tone?: "pos" | "neg"; sub?: string; small?: boolean }) {
  return (
    <div className="rounded-lg border border-line bg-surface-2 px-2.5 py-2">
      <div className="text-[9px] uppercase tracking-wider text-text-mute">{label}</div>
      <div className={clsx("tnum font-semibold", small ? "text-[13px]" : "text-[15px]", tone === "pos" && "text-pos", tone === "neg" && "text-neg", !tone && "text-text")}>{value}</div>
      {sub && <div className="text-[10px] text-text-mute">{sub}</div>}
    </div>
  );
}

function CumChart({ points }: { points: { at: number; v: number }[] }) {
  const w = 560, h = 150;
  if (points.length === 0) return <div className="grid h-[150px] place-items-center text-[12px] text-text-mute">No realized trades yet.</div>;
  const pts = points.length === 1 ? [{ at: points[0].at - 1, v: 0 }, ...points] : points;
  const vs = pts.map((p) => p.v);
  const min = Math.min(0, ...vs), max = Math.max(0, ...vs);
  const range = Math.max(max - min, 1e-6);
  const x = (i: number) => (i / (pts.length - 1)) * w;
  const y = (v: number) => h - ((v - min) / range) * h;
  const y0 = y(0);
  const line = pts.map((p, i) => `${i === 0 ? "M" : "L"}${x(i)},${y(p.v)}`).join(" ");
  const last = pts[pts.length - 1].v;
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="w-full" preserveAspectRatio="none" height={h}>
      <line x1={0} x2={w} y1={y0} y2={y0} stroke="var(--color-line-strong)" strokeDasharray="3 3" />
      <path d={`${line} L${w},${y0} L0,${y0} Z`} fill={last >= 0 ? "var(--color-pos)" : "var(--color-neg)"} opacity={0.08} />
      <path d={line} fill="none" stroke={last >= 0 ? "var(--color-pos)" : "var(--color-neg)"} strokeWidth={1.5} />
    </svg>
  );
}
