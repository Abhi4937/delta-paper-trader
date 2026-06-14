"use client";

// Stacked position-analytics charts (lightweight-charts v5). The server chooses the
// history resolution (uniform-by-age body + 1s live tail) and stamps net-MTM OHLC per
// point, so there's no client timeframe toggle — a Line/Candle toggle (MTM only) and a
// Net/Legs toggle drive five small panels:
//   MTM    — net (baseline green/red, or candle) + each leg (light lines)   [tall]
//   IV     — ATM IV (bold) + each leg's IV (light)
//   Delta  — net (bold) + each leg (light), BTC
//   Theta  — net (bold), USD/day
//   Vega   — net (bold), USD per 1 vol-point
// Net is bold/bright, legs are light. Real time X-axis, value Y-axis, dashed 0
// reference, and a multi-line crosshair tooltip (date·time + net + each leg).
// Panels share one synchronized time scale: pan/zoom one and they all move.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  BaselineSeries,
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  LineSeries,
  LineStyle,
  createChart,
  createSeriesMarkers,
  type AutoscaleInfo,
  type IChartApi,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import { ChevronDown, ChevronRight } from "lucide-react";
import clsx from "clsx";
import { type OHLC, type Pt, atmPoints, capPoints, dedupBySecond, legPoints, netMtmCandles, netPoints } from "@/lib/chartData";
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

function fmtTime(sec: number): string {
  const d = new Date(sec * 1000);
  return d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}
// date + time for the hover tooltip header (e.g. "09 Jun 14:36:22")
function fmtDateTime(sec: number): string {
  const d = new Date(sec * 1000);
  const date = d.toLocaleDateString("en-GB", { day: "2-digit", month: "short" });
  return `${date} ${fmtTime(sec)}`;
}
// compact day+HH:MM for the max/min header stat (e.g. "14 14:30")
function fmtDayHm(sec: number): string {
  const d = new Date(sec * 1000);
  return `${d.toLocaleDateString("en-GB", { day: "2-digit" })} ${d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })}`;
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
  // net-MTM candles straight from the server's per-point OHLC (resolution is chosen
  // server-side; no client re-bucketing). Only the MTM panel's candle view uses these.
  const netCandles = netMtmCandles(series);
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
            <span className="text-text-dim"> · from {fmtDateTime(series[0].t / 1000)}</span>
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
        </div>
      </div>

      <Panel title="MTM" unit="USD" register={register} height={heightOf("MTM")} collapsed={!!collapsed.MTM} onToggle={() => toggle("MTM")} net={netMtm} netCandles={netCandles} legs={legsFor((x) => x?.pnl ?? 0)} fmt={fMoney} kind="mtm" candle={mtmType === "candle"} zero signColor />
      <Panel title="IV" unit="%" register={register} height={heightOf("IV")} collapsed={!!collapsed.IV} onToggle={() => toggle("IV")} net={[]} netCandles={[]} legs={ivLines} fmt={fPct} kind="line" />
      <Panel title="Delta" unit="BTC" register={register} height={heightOf("Delta")} collapsed={!!collapsed.Delta} onToggle={() => toggle("Delta")} net={netDelta} netCandles={[]} legs={legsFor((x) => x?.delta ?? 0)} fmt={fDelta} kind="line" zero signColor />
      <Panel title="Theta" unit="$/day" register={register} height={heightOf("Theta")} collapsed={!!collapsed.Theta} onToggle={() => toggle("Theta")} net={netTheta} netCandles={[]} legs={legsFor((x) => x?.theta ?? 0)} fmt={fNum} kind="line" zero signColor />
      <Panel title="Vega" unit="$/vol-pt" register={register} height={heightOf("Vega")} collapsed={!!collapsed.Vega} onToggle={() => toggle("Vega")} net={netVega} netCandles={[]} legs={legsFor((x) => x?.vega ?? 0)} fmt={fNum} kind="line" zero signColor />
    </div>
  );
}

interface PanelProps {
  title: string;
  unit: string;
  netLabel?: string;
  register: RegisterFn;
  height: number;
  collapsed: boolean;
  onToggle: () => void;
  net: Pt[];
  netCandles: OHLC[]; // server net-MTM OHLC (MTM panel only; [] elsewhere)
  legs?: LegLine[];
  fmt: (v: number) => string;
  kind: "mtm" | "line";
  candle?: boolean;
  zero?: boolean;
  signColor?: boolean; // color the net tooltip value green/red by sign
}

function Panel({ title, unit, netLabel = "Net", register, height, collapsed, onToggle, net, netCandles, legs = [], fmt, kind, candle, zero, signColor }: PanelProps) {
  const [chartVer, setChartVer] = useState(0); // bumps each time the chart is (re)created
  const wrapRef = useRef<HTMLDivElement>(null);
  const tipRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const primaryRef = useRef<ISeriesApi<"Baseline"> | ISeriesApi<"Candlestick"> | ISeriesApi<"Line"> | null>(null);
  const legRefs = useRef<ISeriesApi<"Line">[]>([]);
  const legMeta = useRef<{ label: string; color: string }[]>([]);
  const live = useRef({ fmt, netLabel, signColor });
  live.current = { fmt, netLabel, signColor };
  const nLegs = legs.length;
  // fingerprint of the last render we drew, so we redraw ONLY when something actually
  // changed (view identity OR the data in net OR any leg). Keyed on net+legs length, last
  // t AND last value — so the IV panel (data lives only in `legs`, net is empty) advances,
  // and a same-timestamp value correction still repaints.
  const sigRef = useRef("");
  const dataSigRef = useRef("");
  // fit start→latest ONCE per chart instance (first non-empty load); afterwards the
  // user's pan/zoom is preserved across live ticks instead of being snapped back.
  const fittedRef = useRef(false);
  // ▲/▼ max & min markers on the primary series + a header stat (value @ time).
  const markersRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const ext = useMemo(() => {
    if (net.length === 0) return null;
    let hi = net[0];
    let lo = net[0];
    for (const p of net) {
      if (p.v > hi.v) hi = p;
      if (p.v < lo.v) lo = p;
    }
    return { hi, lo };
  }, [net]);

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
        tickMarkFormatter: (t: Time) => fmtTime(t as number),
      },
      crosshair: {
        mode: CrosshairMode.Magnet,
        vertLine: { color: C.lineStrong, width: 1, labelBackgroundColor: C.surface, labelVisible: true },
        horzLine: { color: C.lineStrong, labelBackgroundColor: C.surface },
      },
      localization: {
        priceFormatter: (p: number) => live.current.fmt(p),
        timeFormatter: (t: Time) => fmtTime(t as number),
      },
      // TradingView-style pan/zoom: wheel + drag to zoom/pan time, drag the price axis
      // for vertical zoom. The chart fits start→latest ONCE on load (see fittedRef); after
      // that the user's zoom/pan is preserved across live ticks. Double-click resets to fit.
      handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
      handleScale: { axisPressedMouseMove: true, mouseWheel: true, pinch: true },
    });
    chartRef.current = chart;
    fittedRef.current = false; // fresh chart → fit once when its data loads
    // double-click anywhere → reset zoom (fit time + re-enable price autoscale)
    const onDblClick = () => {
      chart.timeScale().fitContent();
      chart.priceScale("right").applyOptions({ autoScale: true });
    };
    el.addEventListener("dblclick", onDblClick);
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
      const { fmt: f, netLabel: nl, signColor: sc } = live.current;
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
      tip.innerHTML = `<div style="color:${C.textMute};margin-bottom:3px">${fmtDateTime(param.time as number)}</div>${rows.join("")}`;
      tip.style.opacity = "1";
      // place the box on the side AWAY from the cursor so it never covers the point you're
      // hovering (the old clamp pinned it over the data at the start/end of the chart).
      const w = tip.offsetWidth || 140;
      const cx = param.point.x;
      const x = cx > el.clientWidth / 2 ? cx - w - 16 : cx + 16;
      tip.style.transform = `translateX(${Math.min(Math.max(x, 4), el.clientWidth - w - 4)}px)`;
    });

    return () => {
      unregister();
      ro.disconnect();
      el.removeEventListener("dblclick", onDblClick);
      chart.remove();
      chartRef.current = null;
      primaryRef.current = null;
      legRefs.current = [];
    };
    // legs labels are stable per position; excluded so live ticks don't rebuild the chart
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nLegs, register, collapsed]);

  // resize the chart in place when its height changes (collapse/expand of others).
  // No fitContent here — that would reset the user's pan/zoom on every panel toggle.
  useEffect(() => {
    chartRef.current?.applyOptions({ height });
  }, [height]);

  // (re)create the primary (net) series when MTM line/candle toggles. Panels with
  // no single net (IV — it shows per-expiry ATM lines via `legs`) skip the primary.
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    if (primaryRef.current) {
      chart.removeSeries(primaryRef.current);
      primaryRef.current = null;
      markersRef.current = null; // markers plugin was attached to the removed series
    }
    if (net.length === 0) return; // IV panel has no single "net" series; unified effect loads the legs
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
    markersRef.current = createSeriesMarkers(primaryRef.current, []); // ▲/▼ extremes, set in loadData
    // data load happens in the unified effect below (also keyed on chartVer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [candle, kind, chartVer]);

  // setData the primary + leg series, capped to ~1500 points each. These charts are
  // fixed-view (no zoom; whole trade in a few hundred px) so the cap is visually lossless,
  // and it keeps per-tick setData cheap — the uncapped full-array setData over a multi-hour
  // 1s series, every second across 5 panels, was the per-second hang.
  function loadData() {
    if (!chartRef.current) return;
    const s = primaryRef.current;
    if (s) {
      if (kind === "mtm" && candle) {
        // server already chose the resolution + stamped OHLC → render directly
        (s as ISeriesApi<"Candlestick">).setData(capPoints(netCandles));
      } else {
        (s as ISeriesApi<"Baseline">).setData(
          capPoints(dedupBySecond(net.map((p) => ({ time: Math.floor(p.t / 1000) as UTCTimestamp, value: p.v })))),
        );
      }
    }
    legRefs.current.forEach((ls, i) =>
      ls.setData(
        capPoints(
          dedupBySecond((legs[i]?.pts ?? []).map((p) => ({ time: Math.floor(p.t / 1000) as UTCTimestamp, value: p.v }))),
        ),
      ),
    );
    // ▲ max / ▼ min markers on the primary series (skip when hi == lo or no data)
    if (markersRef.current) {
      const marks: SeriesMarker<Time>[] = [];
      if (ext && ext.hi.t !== ext.lo.t) {
        const lo = { time: Math.floor(ext.lo.t / 1000) as UTCTimestamp, position: "belowBar" as const, color: C.neg, shape: "arrowUp" as const, text: fmt(ext.lo.v) };
        const hi = { time: Math.floor(ext.hi.t / 1000) as UTCTimestamp, position: "aboveBar" as const, color: C.pos, shape: "arrowDown" as const, text: fmt(ext.hi.v) };
        marks.push(...[lo, hi].sort((a, b) => (a.time as number) - (b.time as number)));
      }
      markersRef.current.setMarkers(marks);
    }
  }
  function fitView() {
    chartRef.current?.timeScale().fitContent();
  }
  // Single data effect. setData (never per-tick update() — that corrupts the shared time
  // scale across the multi-series panels and throws null in paint). Skip ONLY when both the
  // view identity AND the data fingerprint are unchanged; otherwise reload + fit so the WHOLE
  // trade (entry→latest) stays in view. The fingerprint covers net AND every leg's count /
  // last-t / last-value — so the IV panel (net is empty; data lives in legs) keeps advancing,
  // and a same-timestamp value correction still repaints. The cap above keeps this cheap.
  useEffect(() => {
    if (!chartRef.current) return;
    const sig = `${candle}|${kind}|${chartVer}`;
    const lastNet = net[net.length - 1];
    const dataSig =
      `${net.length}:${lastNet?.t ?? ""}:${lastNet?.v ?? ""}|` +
      legs
        .map((l) => {
          const p = l.pts[l.pts.length - 1];
          return `${l.pts.length}:${p?.t ?? ""}:${p?.v ?? ""}`;
        })
        .join(",");
    if (sig !== sigRef.current || dataSig !== dataSigRef.current) {
      loadData();
      // fit once on the first load that actually has data; after that keep the user's view
      const hasData = net.length > 0 || legs.some((l) => l.pts.length > 0);
      if (!fittedRef.current && hasData) {
        fitView();
        fittedRef.current = true;
      }
    }
    sigRef.current = sig;
    dataSigRef.current = dataSig;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [net, netCandles, legs, candle, chartVer]);

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
        {ext && (
          <span className="ml-auto flex items-center gap-2 normal-case tnum text-[8px]">
            <span className="text-pos">H {fmt(ext.hi.v)} <span className="text-text-mute">{fmtDayHm(ext.hi.t / 1000)}</span></span>
            <span className="text-neg">L {fmt(ext.lo.v)} <span className="text-text-mute">{fmtDayHm(ext.lo.t / 1000)}</span></span>
          </span>
        )}
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
