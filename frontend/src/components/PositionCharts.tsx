"use client";

// Stacked position-analytics charts (lightweight-charts v5). One shared timeframe
// (1s / 1m / 5m) + a Line/Candle toggle (MTM only) drive five small panels:
//   MTM    — net (baseline green/red, or candle) + each leg (light lines)   [tall]
//   IV     — ATM IV (bold) + each leg's IV (light)
//   Delta  — net (bold) + each leg (light), BTC
//   Theta  — net (bold), USD/day
//   Vega   — net (bold), USD per 1 vol-point
// Net is bold/bright, legs are light. Real time X-axis, value Y-axis, dashed 0
// reference, and a multi-line crosshair tooltip (date·time + net + each leg).
// Panels share one synchronized time scale: pan/zoom one and they all move.

import { useCallback, useEffect, useRef, useState } from "react";
import {
  BaselineSeries,
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  LineSeries,
  LineStyle,
  createChart,
  type AutoscaleInfo,
  type IChartApi,
  type ISeriesApi,
  type Time,
} from "lightweight-charts";
import { ChevronDown, ChevronRight } from "lucide-react";
import clsx from "clsx";
import { type Pt, type TF, atmPoints, buckets, legPoints, netPoints, toLine } from "@/lib/chartData";
import { atmColorFor, legColorMap } from "@/lib/legColors";
import { type Currency, money } from "@/lib/store";
import type { Leg, SeriesSample } from "@/lib/types";

type ChartType = "line" | "candle";
type LegLine = { id: string; label: string; color: string; pts: Pt[]; width?: number };
type RegisterFn = (chart: IChartApi) => () => void;

// theme tokens — mirror frontend/src/app/globals.css (canvas can't read CSS vars)
const C = {
  pos: "#33b991",
  neg: "#ff5c5c",
  net: "#eef1f7", // bright neutral — the bold "net" line (was orange)
  line: "#2c2e38",
  lineStrong: "#3a3d49",
  textDim: "#c2c7d0",
  textMute: "#848a94",
  surface: "#1e2027",
};

function expShort(iso: string): string {
  const d = new Date(iso + "T00:00:00Z");
  return `${d.getUTCDate()}${d.toLocaleString("en", { month: "short", timeZone: "UTC" })}`;
}
function legLabel(l: Leg): string {
  // strike + CE/PE + expiry (so multi-expiry strategies disambiguate on hover/legend)
  return `${l.strike} ${l.type === "call" ? "CE" : "PE"} · ${expShort(l.expiry)}`;
}

function fmtTime(sec: number, tf: TF): string {
  const d = new Date(sec * 1000);
  return tf === 1
    ? d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit" })
    : d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
}
// date + time for the hover tooltip header (e.g. "09 Jun 14:36:22")
function fmtDateTime(sec: number, tf: TF): string {
  const d = new Date(sec * 1000);
  const date = d.toLocaleDateString("en-GB", { day: "2-digit", month: "short" });
  return `${date} ${fmtTime(sec, tf)}`;
}

// stretch a series' autoscale to always include 0 (so the dashed 0-line shows
// even when the data never crosses 0 — e.g. all-negative theta, all-positive vega)
const zeroAutoscale = (orig: () => AutoscaleInfo | null): AutoscaleInfo | null => {
  const r = orig();
  if (r?.priceRange) {
    r.priceRange.minValue = Math.min(r.priceRange.minValue, 0);
    r.priceRange.maxValue = Math.max(r.priceRange.maxValue, 0);
  }
  return r;
};

export default function PositionCharts({
  series,
  legs,
  currency,
}: {
  series: SeriesSample[];
  legs: Leg[];
  currency: Currency;
}) {
  const [tf, setTf] = useState<TF>(1);
  const [mtmType, setMtmType] = useState<ChartType>("line");
  const [showLegs, setShowLegs] = useState(true); // "Legs" = net + per-leg, "Net" = net only

  // shared time-scale sync: every panel registers its chart so their x-axes stay
  // aligned (they move together whether auto-fitting or user-panned).
  const sync = useRef<{ charts: IChartApi[]; busy: boolean }>({ charts: [], busy: false });
  const register = useCallback<RegisterFn>((chart) => {
    const s = sync.current;
    s.charts.push(chart);
    const ts = chart.timeScale();
    const handler = (range: { from: number; to: number } | null) => {
      if (s.busy || !range) return;
      s.busy = true;
      for (const c of s.charts) if (c !== chart) c.timeScale().setVisibleLogicalRange(range);
      s.busy = false;
    };
    ts.subscribeVisibleLogicalRangeChange(handler);
    return () => {
      ts.unsubscribeVisibleLogicalRangeChange(handler);
      s.charts = s.charts.filter((c) => c !== chart);
    };
  }, []);

  // derive per-metric point arrays from the sampled series
  const netMtm = netPoints(series, "pnl");
  const netDelta = netPoints(series, "delta");
  const netTheta = netPoints(series, "theta");
  const netVega = netPoints(series, "vega");

  // per-leg colors: grouped by expiry, CE/PE as shades; same leg → same color in
  // every panel (so a leg's MTM, IV and Delta lines all share its color).
  const expiries = [...new Set(legs.map((l) => l.expiry))];
  const legColorById = legColorMap(legs);
  const legLines = (pick: (ls: SeriesSample["legs"][string] | undefined) => number): LegLine[] =>
    legs.map((l) => ({
      id: l.id,
      label: legLabel(l),
      color: legColorById[l.id],
      pts: legPoints(series, l.id, pick),
    }));
  // "Net" hides the per-leg lines, "Legs" shows them. Keep a CONSTANT number of leg lines
  // (empty data when hidden) so toggling doesn't change the series count and force a chart
  // re-create — that re-create is what blanked the panel until a timeframe change.
  const hide = (ll: LegLine): LegLine => (showLegs ? ll : { ...ll, pts: [] });
  const legsFor = (pick: (ls: SeriesSample["legs"][string] | undefined) => number): LegLine[] =>
    legLines(pick).map(hide);

  // IV panel: bold ATM-IV line per expiry (always) + each leg's IV (hidden in "Net" mode)
  const ivAtm: LegLine[] = expiries.map((e) => ({
    id: `atm-${e}`,
    label: `ATM ${e.slice(5)}`,
    color: atmColorFor(e, expiries),
    width: 2,
    pts: atmPoints(series, e),
  }));
  const ivLines: LegLine[] = [...ivAtm, ...legLines((x) => (x?.iv ?? 0) * 100).map(hide)];

  const fMoney = (v: number) => (v < 0 ? "-" : "+") + money(Math.abs(v), currency);
  const fPct = (v: number) => `${v.toFixed(1)}%`;
  const fDelta = (v: number) => (v >= 0 ? "+" : "") + v.toFixed(3);
  const fNum = (v: number) => (v >= 0 ? "+" : "") + v.toFixed(2);

  // collapse state (lifted so the expanded panels grow to fill the freed space).
  // Each expanded panel gets max(MIN, BUDGET / #expanded): 5 open → tall+scroll,
  // collapse some → the rest grow, 1 open → it fills the budget.
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const toggle = (t: string) => setCollapsed((c) => ({ ...c, [t]: !c[t] }));
  const TITLES = ["MTM", "IV", "Delta", "Theta", "Vega"];
  const WEIGHT: Record<string, number> = { MTM: 1, IV: 1, Delta: 1, Theta: 0.6, Vega: 0.6 };
  const MINH: Record<string, number> = { MTM: 248, IV: 248, Delta: 248, Theta: 150, Vega: 150 };
  // cap so a single open panel doesn't balloon to the whole budget (e.g. MTM alone)
  const MAXH: Record<string, number> = { MTM: 360, IV: 360, Delta: 360, Theta: 240, Vega: 240 };
  const totalW = TITLES.reduce((s, t) => (collapsed[t] ? s : s + WEIGHT[t]), 0) || 1;
  // each expanded panel: clamp(min, share-of-budget, max) — collapsing others grows the
  // rest but never past MAXH. Theta/Vega are smaller (lower weight + lower cap).
  const heightOf = (t: string) =>
    Math.min(MAXH[t], Math.max(MINH[t], Math.round((660 * WEIGHT[t]) / totalW)));

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-2 text-[9px] uppercase tracking-wider text-text-mute">
        <span>
          analytics
          {series.length > 0 && (
            <span className="text-text-dim"> · from {fmtDateTime(series[0].t / 1000, tf)}</span>
          )}
          {" · net bold · legs light"}
        </span>
        <div className="flex items-center gap-1.5 normal-case">
          <Seg
            value={showLegs ? "legs" : "net"}
            onChange={(v) => setShowLegs(v === "legs")}
            opts={[
              { v: "net", label: "Net" },
              { v: "legs", label: "Legs" },
            ]}
          />
          <Seg
            value={mtmType}
            onChange={setMtmType}
            opts={[
              { v: "line", label: "Line" },
              { v: "candle", label: "Candle" },
            ]}
          />
          <Seg
            value={tf}
            onChange={setTf}
            opts={[
              { v: 1, label: "1s" },
              { v: 60, label: "1m" },
              { v: 300, label: "5m" },
            ]}
          />
        </div>
      </div>

      <Panel title="MTM" unit="USD" tf={tf} register={register} height={heightOf("MTM")} collapsed={!!collapsed.MTM} onToggle={() => toggle("MTM")} net={netMtm} legs={legsFor((x) => x?.pnl ?? 0)} fmt={fMoney} kind="mtm" candle={mtmType === "candle"} zero signColor />
      <Panel title="IV" unit="%" tf={tf} register={register} height={heightOf("IV")} collapsed={!!collapsed.IV} onToggle={() => toggle("IV")} net={[]} legs={ivLines} fmt={fPct} kind="line" />
      <Panel title="Delta" unit="BTC" tf={tf} register={register} height={heightOf("Delta")} collapsed={!!collapsed.Delta} onToggle={() => toggle("Delta")} net={netDelta} legs={legsFor((x) => x?.delta ?? 0)} fmt={fDelta} kind="line" zero signColor />
      <Panel title="Theta" unit="$/day" tf={tf} register={register} height={heightOf("Theta")} collapsed={!!collapsed.Theta} onToggle={() => toggle("Theta")} net={netTheta} legs={legsFor((x) => x?.theta ?? 0)} fmt={fNum} kind="line" zero signColor />
      <Panel title="Vega" unit="$/vol-pt" tf={tf} register={register} height={heightOf("Vega")} collapsed={!!collapsed.Vega} onToggle={() => toggle("Vega")} net={netVega} legs={legsFor((x) => x?.vega ?? 0)} fmt={fNum} kind="line" zero signColor />
    </div>
  );
}

interface PanelProps {
  title: string;
  unit: string;
  netLabel?: string;
  tf: TF;
  register: RegisterFn;
  height: number;
  collapsed: boolean;
  onToggle: () => void;
  net: Pt[];
  legs?: LegLine[];
  fmt: (v: number) => string;
  kind: "mtm" | "line";
  candle?: boolean;
  zero?: boolean;
  signColor?: boolean; // color the net tooltip value green/red by sign
}

function Panel({ title, unit, netLabel = "Net", tf, register, height, collapsed, onToggle, net, legs = [], fmt, kind, candle, zero, signColor }: PanelProps) {
  const [chartVer, setChartVer] = useState(0); // bumps each time the chart is (re)created
  const wrapRef = useRef<HTMLDivElement>(null);
  const tipRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const primaryRef = useRef<ISeriesApi<"Baseline"> | ISeriesApi<"Candlestick"> | ISeriesApi<"Line"> | null>(null);
  const legRefs = useRef<ISeriesApi<"Line">[]>([]);
  const legMeta = useRef<{ label: string; color: string }[]>([]);
  const live = useRef({ tf, fmt, netLabel, signColor });
  live.current = { tf, fmt, netLabel, signColor };
  const nLegs = legs.length;

  // create chart + the (fixed-count) per-leg line series (re-runs on expand)
  useEffect(() => {
    const el = wrapRef.current;
    if (!el || collapsed) return;
    const chart = createChart(el, {
      width: el.clientWidth,
      height,
      layout: {
        background: { type: ColorType.Solid, color: "rgba(0,0,0,0)" },
        textColor: C.textMute,
        fontSize: 9,
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: C.line, style: LineStyle.Dotted },
        horzLines: { color: C.line, style: LineStyle.Dotted },
      },
      rightPriceScale: { borderColor: C.line, minimumWidth: 56, scaleMargins: { top: 0.18, bottom: 0.18 } },
      timeScale: {
        visible: true,
        borderColor: C.line,
        timeVisible: true,
        secondsVisible: true,
        rightOffset: 3,
        tickMarkFormatter: (t: Time) => fmtTime(t as number, live.current.tf),
      },
      crosshair: {
        mode: CrosshairMode.Magnet,
        vertLine: { color: C.lineStrong, width: 1, labelBackgroundColor: C.surface, labelVisible: true },
        horzLine: { color: C.lineStrong, labelBackgroundColor: C.surface },
      },
      localization: {
        priceFormatter: (p: number) => live.current.fmt(p),
        timeFormatter: (t: Time) => fmtTime(t as number, live.current.tf),
      },
      // no manual pan/zoom — the chart always auto-fits start→latest so the whole
      // trade is visible in one panel; mouse-wheel scrolls the page, not the chart.
      handleScroll: false,
      handleScale: false,
    });
    chartRef.current = chart;
    setChartVer((v) => v + 1); // trigger the primary-series effect for this fresh chart
    legMeta.current = legs.map((l) => ({ label: l.label, color: l.color }));
    legRefs.current = Array.from({ length: nLegs }, (_, i) =>
      chart.addSeries(LineSeries, {
        color: legs[i]?.color ?? C.textMute,
        lineWidth: (legs[i]?.width ?? 1) as 1 | 2 | 3 | 4,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      }),
    );

    const ro = new ResizeObserver(() => chart.applyOptions({ width: el.clientWidth }));
    ro.observe(el);
    const unregister = register(chart);

    chart.subscribeCrosshairMove((param) => {
      const tip = tipRef.current;
      if (!tip) return;
      if (!param.point || param.time == null) {
        tip.style.opacity = "0";
        return;
      }
      const { fmt: f, tf: t, netLabel: nl, signColor: sc } = live.current;
      const rows: string[] = [];
      const ps = primaryRef.current;
      if (ps && param.seriesData.has(ps)) {
        const d = param.seriesData.get(ps) as { value?: number; close?: number };
        const v = d.value ?? d.close;
        if (v != null) {
          const vc = sc ? (v >= 0 ? C.pos : C.neg) : C.textDim;
          rows.push(row(C.net, nl, f(v), vc));
        }
      }
      legRefs.current.forEach((ls, i) => {
        if (!param.seriesData.has(ls)) return;
        const d = param.seriesData.get(ls) as { value?: number };
        const m = legMeta.current[i];
        if (d.value == null || !m) return;
        // swatch keeps the faint line color; value is green/red by sign on signed
        // panels (MTM/Delta), neutral otherwise (IV)
        const vc = sc ? (d.value >= 0 ? C.pos : C.neg) : C.textDim;
        rows.push(row(m.color, m.label, f(d.value), vc));
      });
      if (!rows.length) {
        tip.style.opacity = "0";
        return;
      }
      tip.innerHTML = `<div style="color:${C.textMute};margin-bottom:3px">${fmtDateTime(param.time as number, t)}</div>${rows.join("")}`;
      tip.style.opacity = "1";
      const x = Math.min(Math.max(param.point.x + 12, 4), el.clientWidth - 140);
      tip.style.transform = `translateX(${x}px)`;
    });

    return () => {
      unregister();
      ro.disconnect();
      chart.remove();
      chartRef.current = null;
      primaryRef.current = null;
      legRefs.current = [];
    };
    // legs labels are stable per position; excluded so live ticks don't rebuild the chart
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nLegs, register, collapsed]);

  // resize the chart in place when its height changes (collapse/expand of others)
  useEffect(() => {
    chartRef.current?.applyOptions({ height });
    chartRef.current?.timeScale().fitContent();
  }, [height]);

  // (re)create the primary (net) series when MTM line/candle toggles. Panels with
  // no single net (IV — it shows per-expiry ATM lines via `legs`) skip the primary.
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    if (primaryRef.current) {
      chart.removeSeries(primaryRef.current);
      primaryRef.current = null;
    }
    if (net.length === 0) {
      loadData();
      return;
    }
    const auto = zero ? { autoscaleInfoProvider: zeroAutoscale } : {};
    if (kind === "mtm" && candle) {
      primaryRef.current = chart.addSeries(CandlestickSeries, {
        upColor: C.pos,
        downColor: C.neg,
        borderVisible: false,
        wickUpColor: C.pos,
        wickDownColor: C.neg,
        priceLineVisible: false,
        ...auto,
      });
    } else if (kind === "mtm") {
      primaryRef.current = chart.addSeries(BaselineSeries, {
        baseValue: { type: "price", price: 0 },
        topLineColor: C.pos,
        topFillColor1: "rgba(51,185,145,0.25)",
        topFillColor2: "rgba(51,185,145,0.02)",
        bottomLineColor: C.neg,
        bottomFillColor1: "rgba(255,92,92,0.02)",
        bottomFillColor2: "rgba(255,92,92,0.25)",
        lineWidth: 2,
        priceLineVisible: false,
        ...auto,
      });
    } else {
      primaryRef.current = chart.addSeries(LineSeries, {
        color: C.net,
        lineWidth: 2,
        priceLineVisible: false,
        ...auto,
      });
    }
    if (zero) {
      primaryRef.current.createPriceLine({
        price: 0,
        color: C.lineStrong,
        lineWidth: 1,
        lineStyle: LineStyle.Solid,
        axisLabelVisible: false,
      });
    }
    loadData();
    fitView();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [candle, kind, chartVer]);

  // setData on the primary + leg series. Fit the view ONLY on create / timeframe change —
  // NEVER on a live tick — so the visible range (and any pan/zoom) doesn't reset every second.
  function loadData() {
    if (!chartRef.current) return;
    const s = primaryRef.current;
    if (s) {
      if (kind === "mtm" && candle) {
        (s as ISeriesApi<"Candlestick">).setData(buckets(net, tf));
      } else {
        (s as ISeriesApi<"Baseline">).setData(toLine(net, tf));
      }
    }
    legRefs.current.forEach((ls, i) => ls.setData(toLine(legs[i]?.pts ?? [], tf)));
  }
  function fitView() {
    chartRef.current?.timeScale().fitContent();
  }
  useEffect(() => {
    loadData();
    fitView();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tf]);
  // re-fit on each update so the WHOLE trade (entry→latest) stays visible as it grows.
  // (Stable now that leg/IV lines carry full points; the sparse lines were the old flicker.)
  useEffect(() => {
    loadData();
    fitView();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [net, legs]);

  return (
    <div className="rounded-lg bg-surface-2/40 px-2 pt-1 pb-0.5 shadow-[0_1px_3px_rgba(0,0,0,0.35)] ring-1 ring-line/50">
      <button
        onClick={onToggle}
        className="flex w-full items-center gap-1.5 text-[9px] text-text-mute hover:text-text-dim"
        title={collapsed ? "Expand" : "Minimize"}
      >
        {collapsed ? <ChevronRight size={11} /> : <ChevronDown size={11} />}
        <span className="font-semibold uppercase tracking-wider text-text-dim">{title}</span>
        <span className="text-[8px] text-text-mute">{unit}</span>
        {netLabel !== "Net" && <span className="text-[8px] text-text-dim">{netLabel}</span>}
      </button>
      {!collapsed && (
        <div ref={wrapRef} className="relative mt-0.5 w-full" style={{ height }}>
          <div
            ref={tipRef}
            className="pointer-events-none absolute left-0 top-0.5 z-10 min-w-[132px] whitespace-nowrap rounded-[5px] border border-line bg-base/95 px-2 py-1 text-[10px] opacity-0 shadow-lg backdrop-blur transition-opacity"
          />
        </div>
      )}
    </div>
  );
}

// one tooltip row: colored swatch + label + right-aligned value
function row(color: string, label: string, value: string, valueColor: string): string {
  return (
    `<div style="display:flex;align-items:center;gap:6px;line-height:1.5">` +
    `<span style="width:8px;height:2px;border-radius:1px;background:${color};flex:none"></span>` +
    `<span style="color:${C.textMute}">${label}</span>` +
    `<b style="margin-left:auto;padding-left:10px;color:${valueColor}">${value}</b>` +
    `</div>`
  );
}

function Seg<T extends string | number>({
  value,
  onChange,
  opts,
}: {
  value: T;
  onChange: (v: T) => void;
  opts: { v: T; label: string }[];
}) {
  return (
    <div className="flex overflow-hidden rounded-[4px] border border-line">
      {opts.map((o) => (
        <button
          key={String(o.v)}
          onClick={() => onChange(o.v)}
          className={clsx(
            "px-1.5 py-0.5 text-[9px] font-medium transition-colors",
            value === o.v ? "bg-surface-3 text-text" : "text-text-mute hover:text-text-dim",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}
