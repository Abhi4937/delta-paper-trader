"use client";

import clsx from "clsx";
import { Minus, Plus, RefreshCw, X } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { type PayoffData, fetchMargin, fetchPayoff } from "@/lib/api";
import { impliedVol } from "@/lib/bs";
import { legIv, legMark, money, moneyBoth, useStore } from "@/lib/store";
import type { Leg } from "@/lib/types";

function legLabel(l: Leg): string {
  return `${l.strike} ${l.type === "call" ? "CE" : "PE"}`;
}

function strategyName(legs: Leg[]): string {
  if (legs.length === 0) return "";
  if (legs.length === 1) {
    const l = legs[0];
    return `${l.side === "buy" ? "Long" : "Short"} ${l.type === "call" ? "Call" : "Put"}`;
  }
  const calls = legs.filter((l) => l.type === "call");
  const puts = legs.filter((l) => l.type === "put");
  if (calls.length === 1 && puts.length === 1 && legs.every((l) => l.strike === legs[0].strike))
    return legs[0].side === "buy" ? "Long Straddle" : "Short Straddle";
  if (calls.length === 1 && puts.length === 1) return "Strangle";
  if (calls.length === 2 && puts.length === 0) return "Call Spread";
  if (puts.length === 2 && calls.length === 0) return "Put Spread";
  if (legs.length === 4) return "Iron Condor / Custom";
  return `Custom · ${legs.length} legs`;
}

// Editable lot input: can be cleared to empty while typing, snaps back to 1 on blur.
function QtyInput({ value, onChange }: { value: number; onChange: (n: number) => void }) {
  const [v, setV] = useState(String(value));
  useEffect(() => setV(String(value)), [value]);
  return (
    <input
      type="text"
      inputMode="numeric"
      value={v}
      onChange={(e) => {
        const raw = e.target.value.replace(/[^0-9]/g, "");
        setV(raw);
        if (raw) onChange(Math.max(1, parseInt(raw)));
      }}
      onBlur={() => {
        if (!v || parseInt(v) < 1) {
          setV("1");
          onChange(1);
        }
      }}
      className="tnum w-12 rounded bg-surface-2 px-1 py-0.5 text-center text-[12px] text-text outline-none focus:ring-1 focus:ring-accent"
    />
  );
}

export default function StrategyBuilder({ onClose }: { onClose: () => void }) {
  const selected = useStore((s) => s.selected);
  const removeLeg = useStore((s) => s.removeLeg);
  const setLegQty = useStore((s) => s.setLegQty);
  const toggleLegSide = useStore((s) => s.toggleLegSide);
  const clearLegs = useStore((s) => s.clearLegs);
  const placeStrategy = useStore((s) => s.placeStrategy);
  const balance = useStore((s) => s.balance);
  const underlying = useStore((s) => s.underlying);
  const currency = useStore((s) => s.currency);
  const feedFresh = useStore((s) => s.feedFresh);
  const spot = useStore((s) => s.chain?.spot ?? 0);
  const router = useRouter();

  const [tab, setTab] = useState<"basket" | "payoff">("basket");
  const [margin, setMargin] = useState<{ value: number; badge: string } | null>(null);
  const [loadingM, setLoadingM] = useState(false);

  const key = selected.map((l) => `${l.productId}:${l.side}:${l.qty}`).join(",");

  useEffect(() => {
    if (selected.length === 0) {
      setMargin(null);
      return;
    }
    let cancelled = false;
    setLoadingM(true);
    const t = setTimeout(async () => {
      const r = await fetchMargin(
        underlying,
        selected.map((l) => ({ product_id: l.productId, side: l.side, size: l.qty })),
      );
      if (!cancelled) {
        setMargin(r ? { value: r.margin, badge: r.badge } : null);
        setLoadingM(false);
      }
    }, 350);
    return () => {
      cancelled = true;
      clearTimeout(t);
    };
  }, [key, underlying, selected]);

  const net = selected.reduce(
    (s, l) => s + (l.side === "sell" ? 1 : -1) * l.qty * l.entry * l.contractValue,
    0,
  );

  function place() {
    if (!feedFresh) return; // never fill at stale/frozen prices
    placeStrategy(strategyName(selected) || "Strategy", {
      target: null,
      stopLossAmount: null,
      stopLossPctOfMargin: null,
      autoExit: false,
      margin: margin?.value,
      badge: (margin?.badge as "matched" | "est" | "stale") ?? "est",
    });
    router.push("/positions"); // go to Positions; set TP/SL on the position card
  }

  return (
    <aside className="flex w-[420px] shrink-0 flex-col border-l border-line bg-surface">
      <div className="flex items-center justify-between border-b border-line px-3 py-2.5">
        <div className="flex items-center gap-2">
          <span className="text-[13px] font-semibold">Strategy Builder</span>
          {selected.length > 0 && (
            <span className="rounded-[4px] bg-surface-3 px-1.5 py-0.5 text-[10px] text-text-dim">
              {strategyName(selected)}
            </span>
          )}
        </div>
        <button onClick={onClose} className="text-text-mute hover:text-text-dim">
          <X size={15} />
        </button>
      </div>

      {selected.length === 0 ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-2 px-6 text-center">
          <Plus size={22} className="text-text-mute" />
          <p className="text-[13px] text-text-dim">Add Contracts from Options Chain</p>
          <p className="text-[11px] text-text-mute">
            Hover a strike and click <span className="font-bold text-pos">B</span> (buy) or{" "}
            <span className="font-bold text-neg">S</span> (sell) — selected legs show here
          </p>
        </div>
      ) : (
        <>
          <div className="flex border-b border-line text-[12px]">
            {(["basket", "payoff"] as const).map((t) => (
              <button
                key={t}
                onClick={() => setTab(t)}
                className={clsx(
                  "flex-1 py-2 font-medium transition-colors",
                  tab === t ? "border-b-2 border-accent text-text" : "text-text-mute hover:text-text-dim",
                )}
              >
                {t === "basket" ? "Orders Basket" : "Analyse Payoff"}
              </button>
            ))}
          </div>

          {tab === "basket" ? (
            <div className="flex min-h-0 flex-1 flex-col">
              <div className="flex items-center justify-between px-3 py-1.5 text-[10px] uppercase tracking-wider text-text-mute">
                <span>{selected.length} legs</span>
                <button onClick={clearLegs} className="hover:text-text-dim">Clear all</button>
              </div>
              <div className="min-h-0 flex-1 overflow-auto">
                {selected.map((l) => (
                  <div key={l.id} className="flex items-center gap-2 border-b border-line/60 px-3 py-2">
                    <button
                      onClick={() => toggleLegSide(l.id)}
                      title="click to flip Buy / Sell"
                      className={clsx(
                        "grid h-5 w-5 place-items-center rounded-[4px] text-[10px] font-bold transition-colors",
                        l.side === "buy" ? "bg-pos/20 text-pos hover:bg-pos/30" : "bg-neg/20 text-neg hover:bg-neg/30",
                      )}
                    >
                      {l.side === "buy" ? "B" : "S"}
                    </button>
                    <div className="min-w-0 flex-1">
                      <div className="text-[12px] font-medium">
                        {legLabel(l)} <span className="text-[10px] text-text-mute">{l.expiry.slice(5)}</span>
                      </div>
                      <div className="flex items-center gap-1 text-[10px] text-text-mute">
                        <span className="rounded bg-surface-3 px-1 text-[9px] font-semibold text-text-dim">M</span>
                        Market Price <span className="tnum">· ${l.entry.toFixed(1)}</span>
                      </div>
                    </div>
                    <div className="flex items-center gap-1">
                      <button onClick={() => setLegQty(l.id, l.qty - 1)} className="grid h-5 w-5 place-items-center rounded bg-surface-2 text-text-dim hover:text-text">
                        <Minus size={11} />
                      </button>
                      <QtyInput value={l.qty} onChange={(n) => setLegQty(l.id, n)} />
                      <button onClick={() => setLegQty(l.id, l.qty + 1)} className="grid h-5 w-5 place-items-center rounded bg-surface-2 text-text-dim hover:text-text">
                        <Plus size={11} />
                      </button>
                    </div>
                    <span className="tnum w-9 text-right text-[10px] text-text-mute">lot</span>
                    <button onClick={() => removeLeg(l.id)} className="text-text-mute hover:text-neg">
                      <X size={13} />
                    </button>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <PayoffView />
          )}

          {/* footer: margin + actions */}
          <div className="border-t border-line px-3 py-2.5">
            <div className="mb-1 flex items-center justify-between text-[12px]">
              <span className="flex items-center gap-1 text-text-dim">
                Order Margin
                {loadingM && <RefreshCw size={11} className="animate-spin text-text-mute" />}
              </span>
              <span className="tnum font-semibold">
                {margin ? moneyBoth(margin.value, currency) : loadingM ? "…" : "—"}
                {margin && (
                  <span
                    className={clsx(
                      "ml-1.5 rounded-[3px] px-1 py-0.5 text-[9px] uppercase",
                      margin.badge === "matched" ? "bg-pos/15 text-pos" : "bg-warn/15 text-warn",
                    )}
                  >
                    {margin.badge === "matched" ? "exact" : margin.badge}
                  </span>
                )}
              </span>
            </div>
            <div className="mb-2 flex items-center justify-between text-[11px] text-text-mute">
              <span>Net {net >= 0 ? "credit" : "debit"}</span>
              <span className={clsx("tnum", net >= 0 ? "text-pos" : "text-neg")}>
                {money(Math.abs(net), currency)}
              </span>
            </div>
            <div className="mb-2 flex items-center justify-between text-[11px] text-text-mute">
              <span>Available</span>
              <span className="tnum">{money(balance, currency)}</span>
            </div>
            {!feedFresh && (
              <div className="mb-2 rounded-[5px] border border-warn/40 bg-warn/10 px-2.5 py-1.5 text-[11px] text-warn">
                ⚠ Delta feed is stale — prices may be frozen. Order placement is blocked until it’s live again (so you can’t fill at bad prices).
              </div>
            )}
            <button
              onClick={place}
              disabled={!feedFresh}
              className={clsx(
                "w-full rounded-[5px] py-2 text-[13px] font-semibold transition-opacity",
                feedFresh ? "bg-accent text-base hover:opacity-90" : "cursor-not-allowed bg-surface-3 text-text-mute",
              )}
            >
              {feedFresh ? "Place Paper Order" : "Data stale — placement blocked"}
            </button>
          </div>
        </>
      )}
    </aside>
  );
}

function PayoffView() {
  const selected = useStore((s) => s.selected);
  const currency = useStore((s) => s.currency);
  const spot = useStore((s) => s.chain?.spot ?? 0);
  const baseDte = selected[0]?.dte ?? 7;

  // null = follow the default (live spot / basket DTE); a number = user override
  const [targetSpot, setTargetSpot] = useState<number | null>(null);
  const [dteOverride, setDteOverride] = useState<number | null>(null);
  const [ivShift, setIvShift] = useState(0); // vol points
  const [data, setData] = useState<PayoffData | null>(null);

  // FETCH range: contains the strikes + spot (strike-based, so it stays stable as
  // the scenario changes — no refetch loop; the breakevens fall inside it).
  const strikes = selected.map((l) => l.strike).filter((s) => s > 0);
  const aF = [spot, ...strikes].filter((x) => x > 0);
  const aMinF = aF.length ? Math.min(...aF) : spot;
  const aMaxF = aF.length ? Math.max(...aF) : spot;
  const padF = Math.max(0.18 * spot, 0.8 * (aMaxF - aMinF));
  const lo = spot > 0 ? Math.max(aMinF - padF, 1) : 0;
  const hi = spot > 0 ? aMaxF + padF : 0;
  const ts = targetSpot ?? spot;
  const dte = dteOverride ?? baseDte;
  const key = selected.map((l) => `${l.productId}:${l.side}:${l.qty}`).join(",");

  // one debounced backend call per slider settle (no per-pixel round-trips)
  useEffect(() => {
    if (selected.length === 0 || spot <= 0) return; // empty basket → placeholder (below)
    const h = setTimeout(async () => {
      const d = await fetchPayoff({
        legs: selected.map((l) => ({
          option_type: l.type, side: l.side, qty: l.qty, strike: l.strike, entry: l.entry,
          // calibrate each leg's vol to its live mark so the curve passes through the
          // real mark at the current spot (projected-at-now == real entry slippage)
          iv: impliedVol(l.type, spot, l.strike, l.dte / 365, legMark(l)) || legIv(l) || 0.5,
          contract_value: l.contractValue,
        })),
        spot: ts, lo, hi, points: 161, t_years: Math.max(dte, 0) / 365, iv_shift: ivShift / 100,
      });
      setData(d);
    }, 180);
    return () => clearTimeout(h);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, ts, dte, ivShift, lo, hi, spot]);

  if (selected.length === 0)
    return <div className="p-4 text-[12px] text-text-mute">Add legs to analyse the payoff.</div>;

  const w = 348, hgt = 168;
  // DISPLAY window: zoom to the breakevens (± a few spots), with spot + target in
  // view. The y-axis fits only this window, so the trough/breakevens aren't dwarfed
  // by the long wings.
  const bes = data?.breakevens?.length ? data.breakevens : strikes;
  const aV = [spot, ts, ...bes].filter((x) => x > 0);
  const vMin = aV.length ? Math.min(...aV) : lo;
  const vMax = aV.length ? Math.max(...aV) : hi;
  const vPad = Math.max((vMax - vMin) * 0.2, spot * 0.015);
  const viewLo = Math.max(vMin - vPad, lo);
  const viewHi = Math.min(vMax + vPad, hi);

  const idxs = data ? data.spots.map((_, i) => i).filter((i) => data.spots[i] >= viewLo && data.spots[i] <= viewHi) : [];
  const winVals = data && idxs.length ? [...idxs.map((i) => data.expiry[i]), ...idxs.map((i) => data.projected[i]), 0] : [0];
  const minP = Math.min(...winVals), maxP = Math.max(...winVals);
  const range = Math.max(maxP - minP, 1e-6);
  const x = (s: number) => (viewHi > viewLo ? ((s - viewLo) / (viewHi - viewLo)) * w : 0);
  const y = (v: number) => hgt - ((v - minP) / range) * hgt;
  const y0 = y(0);
  const tsX = x(Math.min(Math.max(ts, viewLo), viewHi));
  const path = (arr?: number[]) =>
    data && arr ? idxs.map((i, k) => `${k === 0 ? "M" : "L"}${x(data.spots[i])},${y(arr[i])}`).join(" ") : "";
  const nearest = (s: number) =>
    data ? data.spots.reduce((b, sp, i) => (Math.abs(sp - s) < Math.abs(data.spots[b] - s) ? i : b), 0) : 0;
  const tsIdx = nearest(ts);
  const tsExpiry = data?.expiry[tsIdx] ?? 0;
  const tsProj = data?.projected[tsIdx] ?? 0;
  const levels = [0.9, 0.95, 1.0, 1.05, 1.1].map((m) => spot * m);

  return (
    <div className="min-h-0 flex-1 space-y-3 overflow-auto px-3 py-3">
      <div className="grid grid-cols-2 gap-2 text-[11px]">
        <Metric label="Max Profit" value={data ? money(data.max_profit, currency) : "—"} tone="pos" />
        <Metric label="Max Loss" value={data ? money(data.max_loss, currency) : "—"} tone="neg" />
        <Metric label="Breakevens" value={data?.breakevens.length ? data.breakevens.map((b) => b.toFixed(0)).join(", ") : "—"} />
        <Metric label="R / R" value={data && data.max_loss !== 0 ? Math.abs(data.max_profit / data.max_loss).toFixed(2) : "—"} />
      </div>

      <svg width={w} height={hgt}>
        <line x1={0} x2={w} y1={y0} y2={y0} stroke="var(--color-line-strong)" strokeDasharray="3 3" />
        {spot >= viewLo && spot <= viewHi && <line x1={x(spot)} x2={x(spot)} y1={0} y2={hgt} stroke="var(--color-text-mute)" strokeWidth={1} strokeDasharray="2 2" />}
        <line x1={tsX} x2={tsX} y1={0} y2={hgt} stroke="var(--color-accent)" strokeWidth={1} />
        <path d={path(data?.expiry)} fill="none" stroke="var(--color-text)" strokeWidth={1.5} />
        <path d={path(data?.projected)} fill="none" stroke="var(--color-accent)" strokeWidth={1.5} strokeDasharray="4 3" />
        {data?.breakevens.filter((b) => b >= viewLo && b <= viewHi).map((b, i) => (
          <circle key={i} cx={x(b)} cy={y0} r={2.5} fill="var(--color-text-dim)" />
        ))}
        {data && <circle cx={tsX} cy={y(tsProj)} r={3.5} fill="var(--color-accent)" />}
      </svg>
      <div className="flex justify-between text-[10px] tnum text-text-mute">
        <span>{viewLo.toFixed(0)}</span>
        <span className="text-accent">target {ts.toFixed(0)}</span>
        <span>{viewHi.toFixed(0)}</span>
      </div>

      {/* live P&L at the target-spot marker (updates as you drag the slider) */}
      <div className="flex items-center justify-between rounded-[5px] bg-surface-2 px-2.5 py-1.5 text-[11px]">
        <span className="text-text-mute">P&amp;L @ target <span className="tnum text-accent">{ts.toFixed(0)}</span></span>
        <span className="flex gap-3">
          <span className="text-text-mute">at&nbsp;expiry <span className={clsx("tnum font-medium", tsExpiry >= 0 ? "text-pos" : "text-neg")}>{data ? money(tsExpiry, currency) : "—"}</span></span>
          <span className="text-text-mute">projected <span className={clsx("tnum font-medium", tsProj >= 0 ? "text-pos" : "text-neg")}>{data ? money(tsProj, currency) : "—"}</span></span>
        </span>
      </div>
      <div className="flex items-center gap-3 text-[10px] text-text-mute">
        <span className="flex items-center gap-1"><span className="inline-block h-0.5 w-3 bg-text" /> at expiry</span>
        <span className="flex items-center gap-1"><span className="inline-block h-0.5 w-3 bg-accent" /> projected (date · IV)</span>
      </div>

      <div className="space-y-2.5 rounded-[6px] border border-line bg-surface-2 p-2.5">
        <Slider label="Target spot" value={ts} min={lo} max={hi || 1} step={(hi - lo) / 200 || 1} display={ts.toFixed(0)} onChange={setTargetSpot} />
        <Slider label="Days to expiry" value={dte} min={0} max={Math.max(baseDte, 1)} step={Math.max(baseDte / 100, 0.01)} display={`${dte.toFixed(dte < 2 ? 2 : 0)}d`} onChange={setDteOverride} />
        <Slider label="IV shift" value={ivShift} min={-20} max={20} step={0.5} display={`${ivShift >= 0 ? "+" : ""}${ivShift.toFixed(1)} pts`} onChange={setIvShift} />
        {(targetSpot !== null || dteOverride !== null || ivShift !== 0) && (
          <button onClick={() => { setTargetSpot(null); setDteOverride(null); setIvShift(0); }} className="text-[10px] text-accent hover:underline">
            reset scenario
          </button>
        )}
      </div>

      <div className="grid grid-cols-4 gap-2 text-[11px]">
        <Metric label="Net Δ" value={data ? data.greeks.delta.toFixed(3) : "—"} />
        <Metric label="Net Γ" value={data ? data.greeks.gamma.toFixed(5) : "—"} />
        <Metric label="Net Θ" value={data ? data.greeks.theta.toFixed(2) : "—"} />
        <Metric label="Net V" value={data ? data.greeks.vega.toFixed(2) : "—"} />
      </div>

      <div className="rounded-[6px] border border-line bg-surface-2 text-[11px]">
        <div className="grid grid-cols-3 gap-2 border-b border-line px-2.5 py-1 text-[9px] uppercase tracking-wider text-text-mute">
          <span>Spot</span><span className="text-right">At expiry</span><span className="text-right">Projected</span>
        </div>
        {levels.map((s, i) => {
          const idx = nearest(s);
          const e = data?.expiry[idx] ?? 0;
          const pj = data?.projected[idx] ?? 0;
          return (
            <div key={i} className="grid grid-cols-3 gap-2 border-t border-line/40 px-2.5 py-1">
              <span className="tnum">{s.toFixed(0)}</span>
              <span className={clsx("tnum text-right", e >= 0 ? "text-pos" : "text-neg")}>{data ? money(e, currency) : "—"}</span>
              <span className={clsx("tnum text-right", pj >= 0 ? "text-pos" : "text-neg")}>{data ? money(pj, currency) : "—"}</span>
            </div>
          );
        })}
      </div>

      <p className="text-[10px] text-text-mute">
        Projected = Black-76 theoretical P&amp;L at the chosen days-to-expiry &amp; IV shift; greeks at the target spot.
      </p>
    </div>
  );
}

function Slider({
  label, value, min, max, step, display, onChange,
}: { label: string; value: number; min: number; max: number; step: number; display: string; onChange: (v: number) => void }) {
  return (
    <div>
      <div className="mb-0.5 flex items-center justify-between text-[10px]">
        <span className="text-text-mute">{label}</span>
        <span className="tnum text-text-dim">{display}</span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="h-1 w-full cursor-pointer appearance-none rounded bg-surface-3 accent-accent"
      />
    </div>
  );
}

function Metric({ label, value, tone }: { label: string; value: string; tone?: "pos" | "neg" }) {
  return (
    <div className="rounded-[5px] border border-line bg-surface-2 px-2 py-1.5">
      <div className="text-[9px] uppercase tracking-wider text-text-mute">{label}</div>
      <div className={clsx("tnum text-[12px] font-semibold", tone === "pos" && "text-pos", tone === "neg" && "text-neg")}>
        {value}
      </div>
    </div>
  );
}
