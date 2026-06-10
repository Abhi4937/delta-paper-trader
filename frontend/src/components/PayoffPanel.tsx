"use client";

// Sensibull-style Analyse Payoff for the basket: tabbed (Payoff / P&L Table /
// Greeks), with OI bars behind the curve, On-Expiry + On-Target-Date curves, a
// projected-profit pill, current-price + SD bands, strikewise IVs, a date-time
// picker, and per-leg greeks with a lot toggle. Compact. Backend = /api/payoff
// (Black-Scholes r=0); per-leg IV calibrated to the live mark.

import { type ReactNode, useEffect, useId, useState } from "react";
import clsx from "clsx";
import { type PayoffData, fetchPayoff } from "@/lib/api";
import { greeks as bsGreeks, impliedVol } from "@/lib/bs";
import { type Currency, legBasketPrice, legMark, money, useStore } from "@/lib/store";
import type { Leg, OptionChain } from "@/lib/types";

type Tab = "graph" | "table" | "greeks";
const SLIDER = "h-1 w-full cursor-pointer appearance-none rounded bg-surface-3 accent-accent";
const clampN = (v: number, lo: number, hi: number) => Math.min(Math.max(v, lo), hi);

export default function PayoffPanel({ legs }: { legs?: Leg[] } = {}) {
  const storeSelected = useStore((s) => s.selected);
  const selected = legs ?? storeSelected; // analyse a placed position, or the builder basket
  const isBasket = !legs; // basket legs aren't filled yet → price them off the LIVE feed
  useStore((s) => s.tickN); // basket: re-render on chain ticks so the live entry tracks
  // entry cost: a placed leg has a fixed fill; an unplaced basket leg uses the live would-fill
  const entryOf = (l: Leg) => (isBasket ? legBasketPrice(l) : l.entry);
  const currency = useStore((s) => s.currency);
  const chain = useStore((s) => s.chain);
  const spot = chain?.spot ?? 0;
  const atmIv = chain?.atmIv ?? 0;
  // The FRONT (earliest) expiry drives the date slider + the "on expiry" reference.
  const frontLeg = selected.reduce<Leg | undefined>((a, l) => (!a || l.dte < a.dte ? l : a), undefined);
  const baseDte = frontLeg?.dte ?? 7;
  const expiryMs = frontLeg?.expiry ? new Date(`${frontLeg.expiry}T12:00:00Z`).getTime() : 0;
  // distinct expiry dates, nearest first — aligns 1:1 with the API's per-expiry curves
  const expiryDates = [...new Set(selected.map((l) => l.expiry))].sort();
  const fmtExpiry = (iso: string) => new Date(`${iso}T12:00:00Z`).toLocaleDateString("en-GB", { day: "2-digit", month: "short" });

  const [tab, setTab] = useState<Tab>("graph");
  const [targetSpot, setTargetSpot] = useState<number | null>(null);
  const [dteOverride, setDteOverride] = useState<number | null>(null);
  const [ivShift, setIvShift] = useState(0); // global vol-pt offset
  const [legIvAdj, setLegIvAdj] = useState<Record<string, number>>({}); // per-leg vol-pt
  const [lotMode, setLotMode] = useState<"position" | "perlot">("position");
  const [data, setData] = useState<PayoffData | null>(null);

  const ts = targetSpot ?? spot;
  const dte = dteOverride ?? baseDte; // remaining life of the FRONT expiry at the target date
  const elapsedYears = Math.max(baseDte - dte, 0) / 365; // now → target date
  const spotStep = Math.max(Math.round(spot * 0.0005), 1);
  const sd = spot * atmIv * Math.sqrt(Math.max(baseDte, 0) / 365); // 1 SD expected move by front expiry

  // calibrate each leg's base vol to its live mark; total = base + per-leg adj + global
  const baseIv = (l: Leg) => impliedVol(l.type, spot, l.strike, l.dte / 365, legMark(l)) || 0.5;
  const legSigma = (l: Leg) => Math.max(baseIv(l) + (legIvAdj[l.id] ?? 0) / 100 + ivShift / 100, 1e-3);

  // fetch range — strike-based so it's stable across scenarios
  const strikes = selected.map((l) => l.strike).filter((s) => s > 0);
  const aF = [spot, ...strikes].filter((x) => x > 0);
  const aMinF = aF.length ? Math.min(...aF) : spot;
  const aMaxF = aF.length ? Math.max(...aF) : spot;
  const padF = Math.max(0.18 * spot, 0.8 * (aMaxF - aMinF));
  const lo = spot > 0 ? Math.max(aMinF - padF, 1) : 0;
  const hi = spot > 0 ? aMaxF + padF : 0;
  // basket: bucket the live entry (≥0.5 move) into the key so the curve refreshes
  // as the premium drifts — without re-fetching on every sub-tick.
  const key = selected
    .map((l) => `${l.productId}:${l.side}:${l.qty}:${legIvAdj[l.id] ?? 0}${isBasket ? `:${Math.round(legBasketPrice(l) * 2)}` : ""}`)
    .join(",");

  useEffect(() => {
    if (selected.length === 0 || spot <= 0) return;
    const h = setTimeout(async () => {
      const d = await fetchPayoff({
        legs: selected.map((l) => ({
          option_type: l.type, side: l.side, qty: l.qty, strike: l.strike, entry: entryOf(l),
          iv: Math.max(baseIv(l) + (legIvAdj[l.id] ?? 0) / 100, 1e-3),
          contract_value: l.contractValue, t_years: Math.max(l.dte, 0) / 365,
        })),
        spot: ts, lo, hi, points: 161, elapsed_years: elapsedYears, iv_shift: ivShift / 100,
      });
      setData(d);
    }, 180);
    return () => clearTimeout(h);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, ts, dte, ivShift, lo, hi, spot]);

  if (selected.length === 0)
    return <div className="p-4 text-[12px] text-text-mute">Add legs to analyse the payoff.</div>;

  const dtLabel = expiryMs
    ? new Date(expiryMs - dte * 86_400_000).toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" })
    : "";
  const dteDisplay = dte >= 1 ? `${dte.toFixed(dte < 3 ? 1 : 0)}d` : dte * 24 >= 1 ? `${(dte * 24).toFixed(1)}h` : `${Math.round(dte * 24 * 60)}m`;
  const isLive = Math.abs(ts - spot) < Math.max(spotStep, 1) && dteOverride === null && ivShift === 0 && Object.values(legIvAdj).every((v) => !v);

  const nearest = (s: number) => (data ? data.spots.reduce((b, sp, i) => (Math.abs(sp - s) < Math.abs(data.spots[b] - s) ? i : b), 0) : 0);
  const tsExpiry = data?.expiry[nearest(ts)] ?? 0;
  const tsProj = data?.projected[nearest(ts)] ?? 0;

  // datetime picker value (local-ish ISO without seconds)
  const dtInput = expiryMs ? new Date(expiryMs - dte * 86_400_000).toISOString().slice(0, 16) : "";
  const onPickDate = (v: string) => {
    if (!v || !expiryMs) return;
    const ms = new Date(v).getTime();
    setDteOverride(clampN((expiryMs - ms) / 86_400_000, 0, baseDte));
  };

  return (
    <div className="min-h-0 flex-1 space-y-2 overflow-auto p-2.5 text-[11px]">
      <div className="flex gap-1 rounded-[5px] bg-surface-2 p-0.5 text-[10px]">
        {([["graph", "Payoff"], ["table", "P&L Table"], ["greeks", "Greeks"]] as [Tab, string][]).map(([t, l]) => (
          <button key={t} onClick={() => setTab(t)} className={clsx("flex-1 rounded-[4px] py-1 font-medium transition-colors", tab === t ? "bg-surface-3 text-text" : "text-text-mute hover:text-text-dim")}>
            {l}
          </button>
        ))}
      </div>

      <div className="grid grid-cols-4 gap-1.5">
        <Mini label="Max P" value={data ? money(data.max_profit, currency) : "—"} tone="pos" />
        <Mini label="Max L" value={data ? money(data.max_loss, currency) : "—"} tone="neg" />
        <Mini label="Breakevens" value={data?.breakevens.length ? data.breakevens.map((b) => b.toFixed(0)).join(" · ") : "—"} />
        <Mini label="R / R" value={data && data.max_loss !== 0 ? Math.abs(data.max_profit / data.max_loss).toFixed(2) : "—"} />
      </div>

      {tab === "graph" && (
        <>
          <PayoffChart data={data} chain={chain} spot={spot} ts={ts} sd={sd} lo={lo} hi={hi} tsProj={tsProj} currency={currency} expiryLabels={expiryDates.map(fmtExpiry)} />
          <div className="flex items-center justify-between rounded-[5px] bg-surface-2 px-2 py-1">
            <span className="text-text-mute">
              @ <span className="tnum text-accent">{ts.toFixed(0)}</span>{" "}
              <span className={clsx("rounded-[3px] px-1 text-[8px] uppercase", isLive ? "bg-pos/15 text-pos" : "bg-accent/15 text-accent")}>{isLive ? "now·live" : "what-if"}</span>
            </span>
            <span className="flex gap-2.5">
              <span className="text-text-mute">exp <b className={clsx("tnum", tsExpiry >= 0 ? "text-pos" : "text-neg")}>{data ? money(tsExpiry, currency) : "—"}</b></span>
              <span className="text-text-mute">proj <b className={clsx("tnum", tsProj >= 0 ? "text-pos" : "text-neg")}>{data ? money(tsProj, currency) : "—"}</b></span>
            </span>
          </div>

          <div className="space-y-2 rounded-[6px] border border-line bg-surface-2 p-2">
            <div className="flex items-center justify-between text-[9px] uppercase tracking-wider text-text-mute">
              <span>Scenario</span>
              {(targetSpot !== null || dteOverride !== null || ivShift !== 0 || Object.values(legIvAdj).some(Boolean)) && (
                <button onClick={() => { setTargetSpot(null); setDteOverride(null); setIvShift(0); setLegIvAdj({}); }} className="normal-case text-accent hover:underline">reset all</button>
              )}
            </div>
            <Ctl label="Target spot" sub={spot ? `${(((ts - spot) / spot) * 100).toFixed(2)}%` : ""} onReset={targetSpot !== null ? () => setTargetSpot(null) : undefined}
              stepper={<Step text={ts.toFixed(0)} onStep={(d) => setTargetSpot(clampN(ts + d * spotStep, lo, hi))} />}
              slider={<input type="range" min={lo} max={hi || 1} step={(hi - lo) / 600 || 1} value={ts} onChange={(e) => setTargetSpot(Number(e.target.value))} className={SLIDER} />} />
            <Ctl label="Date / DTE" sub={`${dteDisplay} · ${dtLabel} IST`} onReset={dteOverride !== null ? () => setDteOverride(null) : undefined}
              stepper={<input type="datetime-local" value={dtInput} max={expiryMs ? new Date(expiryMs).toISOString().slice(0, 16) : undefined} onChange={(e) => onPickDate(e.target.value)} className="rounded-[4px] border border-line bg-surface px-1 py-0.5 text-[10px] text-text" />}
              slider={<input type="range" min={0} max={Math.max(baseDte, 0.01)} step={Math.max(baseDte / 400, 0.005)} value={dte} onChange={(e) => setDteOverride(Number(e.target.value))} className={SLIDER} />} />
            <Ctl label="IV shift (all)" sub="vol pts" onReset={ivShift !== 0 ? () => setIvShift(0) : undefined}
              stepper={<Step text={`${ivShift >= 0 ? "+" : ""}${ivShift.toFixed(1)}`} onStep={(d) => setIvShift(clampN(ivShift + d * 0.5, -50, 50))} />}
              slider={<input type="range" min={-20} max={20} step={0.5} value={ivShift} onChange={(e) => setIvShift(Number(e.target.value))} className={SLIDER} />} />

            <div className="border-t border-line/60 pt-1.5">
              <div className="mb-1 flex items-center justify-between text-[9px] uppercase tracking-wider text-text-mute">
                <span>Strikewise IV</span>
                {Object.values(legIvAdj).some(Boolean) && <button onClick={() => setLegIvAdj({})} className="normal-case text-accent hover:underline">reset</button>}
              </div>
              {selected.map((l) => (
                <div key={l.id} className="flex items-center justify-between py-0.5 text-[10px]">
                  <span className="text-text-dim">{l.strike} {l.type === "call" ? "CE" : "PE"} <span className="text-text-mute">{(legSigma(l) * 100).toFixed(1)}%</span></span>
                  <Step text={`${(legIvAdj[l.id] ?? 0) >= 0 ? "+" : ""}${(legIvAdj[l.id] ?? 0).toFixed(1)}`} onStep={(d) => setLegIvAdj((a) => ({ ...a, [l.id]: clampN((a[l.id] ?? 0) + d * 0.5, -50, 50) }))} />
                </div>
              ))}
            </div>
          </div>
        </>
      )}

      {tab === "table" && <PnlTable data={data} spot={spot} sd={sd} strikes={strikes} currency={currency} nearest={nearest} />}
      {tab === "greeks" && <GreeksTab selected={selected} ts={ts} elapsedYears={elapsedYears} legSigma={legSigma} lotMode={lotMode} setLotMode={setLotMode} spot={spot} sd={sd} />}
    </div>
  );
}

// ---- chart ----------------------------------------------------------------
function PayoffChart({
  data, chain, spot, ts, sd, lo, hi, tsProj, currency, expiryLabels,
}: {
  data: PayoffData | null; chain: OptionChain | null; spot: number; ts: number; sd: number;
  lo: number; hi: number; tsProj: number; currency: Currency;
  expiryLabels: string[];
}) {
  const w = 384, hgt = 168;
  // one at-expiry curve per distinct leg expiry (nearest first); the last is the front-most
  // (solid), later expiries lighten. Falls back to the single `expiry` array if absent.
  const expCurves = data ? (data.expiries?.length ? data.expiries : [{ tYears: 0, pnl: data.expiry }]) : [];
  const multiExp = expCurves.length > 1;
  const bes = data?.breakevens?.length ? data.breakevens : [];
  const aV = [spot, ts, ...bes, spot - 2 * sd, spot + 2 * sd].filter((x) => x > 0);
  const vMin = aV.length ? Math.min(...aV) : lo;
  const vMax = aV.length ? Math.max(...aV) : hi;
  const vPad = Math.max((vMax - vMin) * 0.12, spot * 0.01);
  const viewLo = Math.max(vMin - vPad, lo);
  const viewHi = Math.min(vMax + vPad, hi);

  const idxs = data ? data.spots.map((_, i) => i).filter((i) => data.spots[i] >= viewLo && data.spots[i] <= viewHi) : [];
  const winVals = data && idxs.length
    ? [...expCurves.flatMap((c) => idxs.map((i) => c.pnl[i])), ...idxs.map((i) => data.projected[i]), 0]
    : [0];
  const minP = Math.min(...winVals), maxP = Math.max(...winVals);
  const range = Math.max(maxP - minP, 1e-6);
  const x = (s: number) => (viewHi > viewLo ? ((s - viewLo) / (viewHi - viewLo)) * w : 0);
  const y = (v: number) => hgt - ((v - minP) / range) * hgt;
  const y0 = y(0);
  const tsX = x(clampN(ts, viewLo, viewHi));
  const path = (arr?: number[]) => (data && arr ? idxs.map((i, k) => `${k === 0 ? "M" : "L"}${x(data.spots[i])},${y(arr[i])}`).join(" ") : "");
  // front expiry solid white; each later expiry fades. Single-expiry → just white.
  const expColor = (i: number) => (multiExp ? `color-mix(in srgb, var(--color-text) ${Math.round(100 - (i * 55) / Math.max(expCurves.length - 1, 1))}%, var(--color-surface-3))` : "var(--color-text)");

  // OI bars (Call/Put notional) hang from the zero P&L line, faint, behind the curve.
  const oiRows = (chain?.rows ?? []).filter((r) => r.strike >= viewLo && r.strike <= viewHi);
  const maxOi = Math.max(1, ...oiRows.flatMap((r) => [r.call.oiValueUsd, r.put.oiValueUsd]));
  const oiH = (v: number) => Math.min((v / maxOi) * hgt * 0.4, Math.max(hgt - y0, 0));

  const sdLines = sd > 0 ? [-2, -1, 1, 2].map((m) => ({ m, s: spot + m * sd })).filter((d) => d.s >= viewLo && d.s <= viewHi) : [];

  // Front (nearest) expiry is the primary P&L curve: green fill/line in profit (above the
  // zero line), red in loss (below). The filled area is split by clipping at the zero line.
  const front = expCurves[0]?.pnl;
  const areaPath = (arr?: number[]) => {
    if (!data || !arr || idxs.length === 0) return "";
    const line = idxs.map((i, k) => `${k === 0 ? "M" : "L"}${x(data.spots[i])},${y(arr[i])}`).join(" ");
    const x0 = x(data.spots[idxs[0]]), x1 = x(data.spots[idxs[idxs.length - 1]]);
    return `${line} L${x1},${y0} L${x0},${y0} Z`;
  };
  const clipAbove = Math.min(Math.max(y0, 0), hgt); // zero line within the chart box
  const cid = useId().replace(/:/g, ""); // unique, CSS-url-safe clip ids per instance
  const above = `pf-${cid}-a`, below = `pf-${cid}-b`;

  return (
    <div>
      <svg width={w} height={hgt} className="w-full">
        <defs>
          <clipPath id={above}><rect x={0} y={0} width={w} height={clipAbove} /></clipPath>
          <clipPath id={below}><rect x={0} y={clipAbove} width={w} height={hgt - clipAbove} /></clipPath>
        </defs>
        {/* OI bars hanging from the zero line (calls red, puts green), behind the curve */}
        {oiRows.map((r, i) => (
          <g key={i}>
            <rect x={x(r.strike) - 2.5} y={y0} width={2.2} height={oiH(r.call.oiValueUsd)} fill="var(--color-neg)" opacity={0.18} />
            <rect x={x(r.strike) + 0.3} y={y0} width={2.2} height={oiH(r.put.oiValueUsd)} fill="var(--color-pos)" opacity={0.18} />
          </g>
        ))}
        {/* SD bands */}
        {sdLines.map((d) => (
          <g key={d.m}>
            <line x1={x(d.s)} x2={x(d.s)} y1={0} y2={hgt} stroke="var(--color-line-strong)" strokeWidth={1} strokeDasharray="1 4" />
            <text x={x(d.s)} y={9} fill="var(--color-text-mute)" fontSize={7} textAnchor="middle">{d.m > 0 ? `+${d.m}σ` : `${d.m}σ`}</text>
          </g>
        ))}
        {/* green profit fill above the zero line, red loss fill below (front expiry) */}
        <path d={areaPath(front)} fill="var(--color-pos)" opacity={0.16} clipPath={`url(#${above})`} />
        <path d={areaPath(front)} fill="var(--color-neg)" opacity={0.16} clipPath={`url(#${below})`} />
        {/* zero P&L baseline */}
        <line x1={0} x2={w} y1={y0} y2={y0} stroke="var(--color-line-strong)" strokeWidth={1} />
        {spot >= viewLo && spot <= viewHi && <line x1={x(spot)} x2={x(spot)} y1={0} y2={hgt} stroke="var(--color-text-mute)" strokeWidth={1} strokeDasharray="2 2" />}
        <line x1={tsX} x2={tsX} y1={0} y2={hgt} stroke="var(--color-accent)" strokeWidth={1} />
        {/* later expiries: faded dashed lines under the front curve */}
        {expCurves.map((c, i) => i).filter((i) => i > 0).reverse().map((i) => (
          <path key={i} d={path(expCurves[i].pnl)} fill="none" stroke={expColor(i)} strokeWidth={1.3} strokeDasharray="2 2" />
        ))}
        {/* front expiry line: green above the zero line (profit), red below (loss) */}
        <path d={path(front)} fill="none" stroke="var(--color-pos)" strokeWidth={1.8} clipPath={`url(#${above})`} />
        <path d={path(front)} fill="none" stroke="var(--color-neg)" strokeWidth={1.8} clipPath={`url(#${below})`} />
        {/* projected (target-date / now) */}
        <path d={path(data?.projected)} fill="none" stroke="var(--color-accent)" strokeWidth={1.5} strokeDasharray="4 3" />
        {bes.filter((b) => b >= viewLo && b <= viewHi).map((b, i) => <circle key={i} cx={x(b)} cy={y0} r={2.5} fill="var(--color-text-dim)" />)}
        {data && <circle cx={tsX} cy={y(tsProj)} r={3.5} fill="var(--color-accent)" />}
      </svg>
      {/* projected-profit pill at the marker + axis + legend */}
      <div className="relative h-4">
        <span
          className={clsx("absolute -translate-x-1/2 rounded-[3px] px-1 py-0.5 text-[8px] font-semibold", tsProj >= 0 ? "bg-pos/20 text-pos" : "bg-neg/20 text-neg")}
          style={{ left: `${Math.min(Math.max((tsX / w) * 100, 8), 92)}%` }}
        >
          {data ? money(tsProj, currency) : "—"}
        </span>
      </div>
      <div className="flex justify-between text-[9px] tnum text-text-mute">
        <span>{viewLo.toFixed(0)}</span>
        <span className="text-text-mute">current <span className="text-text-dim">{spot.toFixed(0)}</span> · target <span className="text-accent">{ts.toFixed(0)}</span></span>
        <span>{viewHi.toFixed(0)}</span>
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[9px] text-text-mute">
        {expCurves.map((_, i) => (
          <span key={i} className="flex items-center gap-1">
            {i === 0 ? (
              <span className="inline-flex h-0.5 w-3 overflow-hidden">
                <span className="h-0.5 w-1.5" style={{ backgroundColor: "var(--color-pos)" }} />
                <span className="h-0.5 w-1.5" style={{ backgroundColor: "var(--color-neg)" }} />
              </span>
            ) : (
              <span className="inline-block h-0.5 w-3" style={{ backgroundColor: expColor(i) }} />
            )}
            {multiExp ? `Exp ${expiryLabels[i] ?? ""}` : "On expiry"}
          </span>
        ))}
        <span className="flex items-center gap-1"><span className="inline-block h-0 w-3 border-t border-dashed border-accent" /> Target date</span>
        <span className="flex items-center gap-1"><span className="inline-block h-2 w-1 bg-neg/40" /> Call OI</span>
        <span className="flex items-center gap-1"><span className="inline-block h-2 w-1 bg-pos/40" /> Put OI</span>
      </div>
    </div>
  );
}

// ---- P&L table (at SD levels + strikes) -----------------------------------
function PnlTable({
  data, spot, sd, strikes, currency, nearest,
}: { data: PayoffData | null; spot: number; sd: number; strikes: number[]; currency: Currency; nearest: (s: number) => number }) {
  const sdLevels = sd > 0 ? [-2, -1, 0, 1, 2].map((m) => ({ label: m === 0 ? "spot" : `${m > 0 ? "+" : ""}${m}σ`, s: spot + m * sd })) : [{ label: "spot", s: spot }];
  const rows = [...sdLevels, ...strikes.map((s) => ({ label: "K", s }))].sort((a, b) => a.s - b.s);
  return (
    <div className="rounded-[6px] border border-line bg-surface-2">
      <div className="grid grid-cols-[1fr_auto_1fr_1fr] gap-2 border-b border-line px-2.5 py-1 text-[9px] uppercase tracking-wider text-text-mute">
        <span>Spot</span><span></span><span className="text-right">On expiry</span><span className="text-right">On target</span>
      </div>
      {rows.map((r, i) => {
        const idx = nearest(r.s);
        const e = data?.expiry[idx] ?? 0, p = data?.projected[idx] ?? 0;
        return (
          <div key={i} className="grid grid-cols-[1fr_auto_1fr_1fr] items-center gap-2 border-t border-line/40 px-2.5 py-1 text-[11px]">
            <span className="tnum">{r.s.toFixed(0)}</span>
            <span className="text-[8px] uppercase text-text-mute">{r.label}</span>
            <span className={clsx("tnum text-right", e >= 0 ? "text-pos" : "text-neg")}>{data ? money(e, currency) : "—"}</span>
            <span className={clsx("tnum text-right", p >= 0 ? "text-pos" : "text-neg")}>{data ? money(p, currency) : "—"}</span>
          </div>
        );
      })}
    </div>
  );
}

// ---- Greeks tab (net + per-leg, lot toggle, SD/futures) --------------------
function GreeksTab({
  selected, ts, elapsedYears, legSigma, lotMode, setLotMode, spot, sd,
}: {
  selected: Leg[]; ts: number; elapsedYears: number; legSigma: (l: Leg) => number;
  lotMode: "position" | "perlot"; setLotMode: (m: "position" | "perlot") => void; spot: number; sd: number;
}) {
  const perLeg = selected.map((l) => {
    const residual = Math.max(l.dte / 365 - elapsedYears, 0); // per-leg time to expiry at target date
    const g = bsGreeks(l.type, ts, l.strike, residual, legSigma(l));
    const sign = l.side === "buy" ? 1 : -1;
    const k = sign * (lotMode === "position" ? l.qty : 1) * l.contractValue;
    return { l, delta: k * g.delta, gamma: k * g.gamma, theta: k * g.theta, vega: k * g.vega };
  });
  const net = perLeg.reduce((a, r) => ({ delta: a.delta + r.delta, gamma: a.gamma + r.gamma, theta: a.theta + r.theta, vega: a.vega + r.vega }), { delta: 0, gamma: 0, theta: 0, vega: 0 });

  return (
    <div className="space-y-2">
      <div className="flex justify-end">
        <div className="flex overflow-hidden rounded-[4px] border border-line text-[10px]">
          {(["position", "perlot"] as const).map((m) => (
            <button key={m} onClick={() => setLotMode(m)} className={clsx("px-2 py-0.5", lotMode === m ? "bg-surface-3 text-text" : "text-text-mute hover:text-text-dim")}>{m === "position" ? "Position" : "Per lot"}</button>
          ))}
        </div>
      </div>
      <div className="grid grid-cols-4 gap-1.5">
        <Mini label="Net Δ" value={net.delta.toFixed(3)} />
        <Mini label="Net Γ" value={net.gamma.toFixed(5)} />
        <Mini label="Net Θ" value={net.theta.toFixed(2)} />
        <Mini label="Net V" value={net.vega.toFixed(2)} />
      </div>
      <div className="rounded-[6px] border border-line bg-surface-2 text-[10px]">
        <div className="grid grid-cols-[1.3fr_1fr_1fr_1fr_1fr] gap-1 border-b border-line px-2 py-1 text-[8px] uppercase tracking-wider text-text-mute">
          <span>Leg</span><span className="text-right">Δ</span><span className="text-right">Γ</span><span className="text-right">Θ</span><span className="text-right">V</span>
        </div>
        {perLeg.map((r) => (
          <div key={r.l.id} className="grid grid-cols-[1.3fr_1fr_1fr_1fr_1fr] gap-1 border-t border-line/40 px-2 py-1">
            <span className={clsx(r.l.side === "buy" ? "text-pos" : "text-neg")}>{r.l.side === "buy" ? "+" : "−"}{r.l.strike} {r.l.type === "call" ? "CE" : "PE"}</span>
            <span className="tnum text-right">{r.delta.toFixed(3)}</span>
            <span className="tnum text-right">{r.gamma.toFixed(5)}</span>
            <span className="tnum text-right">{r.theta.toFixed(2)}</span>
            <span className="tnum text-right">{r.vega.toFixed(2)}</span>
          </div>
        ))}
      </div>
      <div className="rounded-[6px] border border-line bg-surface-2 p-2 text-[10px]">
        <div className="mb-1 text-[9px] uppercase tracking-wider text-text-mute">Target-day move (±σ)</div>
        <div className="flex justify-between text-text-dim"><span>Underlying (now)</span><span className="tnum">{spot.toFixed(1)}</span></div>
        {sd > 0 ? (
          [1, 2].map((m) => (
            <div key={m} className="flex justify-between text-text-mute">
              <span>{m} SD ({((m * sd) / spot * 100).toFixed(1)}%)</span>
              <span className="tnum">±{(m * sd).toFixed(0)} → {(spot - m * sd).toFixed(0)} / {(spot + m * sd).toFixed(0)}</span>
            </div>
          ))
        ) : (
          <div className="text-text-mute">at expiry (σ→0)</div>
        )}
      </div>
    </div>
  );
}

// ---- small shared bits ----------------------------------------------------
function Mini({ label, value, tone }: { label: string; value: string; tone?: "pos" | "neg" }) {
  return (
    <div className="rounded-[5px] border border-line bg-surface-2 px-1.5 py-1">
      <div className="text-[8px] uppercase tracking-wider text-text-mute">{label}</div>
      <div className={clsx("tnum truncate text-[11px] font-semibold", tone === "pos" && "text-pos", tone === "neg" && "text-neg")}>{value}</div>
    </div>
  );
}

function Ctl({ label, sub, onReset, stepper, slider }: { label: string; sub?: string; onReset?: () => void; stepper: ReactNode; slider: ReactNode }) {
  return (
    <div>
      <div className="mb-0.5 flex items-center justify-between text-[10px]">
        <span className="text-text-mute">{label}</span>
        <div className="flex items-center gap-2">
          {sub && <span className="tnum text-[9px] text-text-dim">{sub}</span>}
          {onReset && <button onClick={onReset} className="text-[9px] text-accent hover:underline">reset</button>}
        </div>
      </div>
      <div className="flex items-center gap-1.5">
        {stepper}
        <div className="min-w-0 flex-1">{slider}</div>
      </div>
    </div>
  );
}

function Step({ text, onStep }: { text: string; onStep: (dir: -1 | 1) => void }) {
  return (
    <div className="flex flex-none items-center rounded-[4px] border border-line">
      <button onClick={() => onStep(-1)} className="px-1 text-text-mute hover:text-text">−</button>
      <span className="tnum w-12 text-center text-[10px] text-text">{text}</span>
      <button onClick={() => onStep(1)} className="px-1 text-text-mute hover:text-text">＋</button>
    </div>
  );
}
