"use client";

import clsx from "clsx";
import { useRouter } from "next/navigation";
import { Fragment, useEffect, useRef, useState } from "react";
import MarketDepthSheet from "@/components/MarketDepthSheet";
import OrderBook from "@/components/OrderBook";
import StrategyBuilder from "@/components/StrategyBuilder";
import { useStore } from "@/lib/store";
import type { Contract, Side } from "@/lib/types";

// OI notional in USD — matches Delta's "$2.04M / $877K" OI column.
function compactOi(n: number): string {
  if (n >= 1e6) return `$${(n / 1e6).toFixed(2)}M`;
  if (n >= 1e3) return `$${(n / 1e3).toFixed(1)}K`;
  return `$${Math.round(n)}`;
}

const HL = "bg-neg/[0.12]"; // Delta-style light-red selection tint

export default function ChainPage() {
  const chain = useStore((s) => s.chain);
  const selected = useStore((s) => s.selected);
  const positions = useStore((s) => s.positions);
  const selectLeg = useStore((s) => s.selectLeg);
  const clearLegs = useStore((s) => s.clearLegs);
  const router = useRouter();
  const prev = useRef<Map<string, number>>(new Map());
  const atmRef = useRef<HTMLTableCellElement>(null);
  const centeredFor = useRef("");
  const [builderMode, setBuilderMode] = useState(false);
  const [focused, setFocused] = useState<string | null>(null);
  // mobile: which mark cell has its B/S revealed (tap-to-reveal, ONE at a time) — keeps the
  // chain clean instead of showing B/S on every row. Desktop still uses hover-reveal.
  const [activeCell, setActiveCell] = useState<string | null>(null);
  // mobile: depth opens as an on-demand bottom sheet (tap a mark in view mode)
  const [depthOpen, setDepthOpen] = useState(false);

  useEffect(() => {
    if (selected.length > 0) setBuilderMode(true);
  }, [selected.length]);

  // reset the transient mobile reveal/sheet when switching build⇄view
  useEffect(() => {
    setActiveCell(null);
    setDepthOpen(false);
  }, [builderMode]);

  useEffect(() => {
    const key = chain ? `${chain.underlying}-${chain.expiry}` : "";
    if (chain && centeredFor.current !== key && atmRef.current) {
      // center the ATM STRIKE cell on BOTH axes — vertically (its row) and horizontally
      // (the strike column), so on a narrow/scrolling viewport the ATM sits dead-center
      // with calls peeking left and puts right. `inline:"center"` adds the horizontal part.
      atmRef.current.scrollIntoView({ block: "center", inline: "center" });
      centeredFor.current = key;
    }
  });

  if (!chain) {
    return (
      <div className="grid h-full place-items-center text-[13px] text-text-mute">
        connecting to Delta…
      </div>
    );
  }

  const flash = new Map<string, "up" | "down">();
  for (const r of chain.rows) {
    for (const c of [r.call, r.put]) {
      const p = prev.current.get(c.symbol);
      if (p !== undefined && c.mark !== p) flash.set(c.symbol, c.mark > p ? "up" : "down");
      prev.current.set(c.symbol, c.mark);
    }
  }
  const sel = new Map<string, Side>();
  for (const l of selected) sel.set(l.symbol, l.side);
  const posMap = new Map<string, Side>();
  for (const p of positions)
    if (p.status === "open") for (const l of p.legs) posMap.set(l.symbol, l.side);

  const oiC = (c: Contract) => c.oiValueUsd;
  const maxCallOi = Math.max(...chain.rows.map((r) => oiC(r.call)), 1);
  const maxPutOi = Math.max(...chain.rows.map((r) => oiC(r.put)), 1);
  const atmCallSymbol = chain.rows.find((r) => r.strike === chain.atmStrike)?.call.symbol ?? null;
  const focusedSym = focused ?? atmCallSymbol;
  const focusedC = chain.rows.flatMap((r) => [r.call, r.put]).find((c) => c.symbol === focusedSym);
  const focusedMark = focusedC?.mark ?? null;
  const focusedLtp = focusedC?.ltp ?? null;

  return (
    <div className="flex h-full flex-col lg:flex-row">
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <div className="flex items-center gap-4 border-b border-line px-4 py-2.5">
          <h1 className="text-[13px] font-semibold tracking-tight text-text">Option Chain</h1>
          <span className="rounded-[5px] border border-line bg-surface px-2 py-0.5 text-[11px] text-text-dim">
            ATM IV <span className="tnum text-warn">{(chain.atmIv * 100).toFixed(1)}%</span>
          </span>
          <span className="text-[11px] text-text-mute">{chain.dte}d · {chain.expiry}</span>
          <button
            onClick={() => setBuilderMode((v) => !v)}
            className="ml-auto flex items-center gap-2 text-[12px] font-medium text-text-dim"
          >
            Strategy Builder
            <span className={clsx("relative h-4 w-7 rounded-full transition-colors", builderMode ? "bg-accent" : "bg-surface-3")}>
              <span className={clsx("absolute top-0.5 h-3 w-3 rounded-full bg-text transition-all", builderMode ? "left-3.5" : "left-0.5")} />
            </span>
            {selected.length > 0 && (
              <span className="grid h-4 min-w-4 place-items-center rounded-full bg-accent px-1 text-[9px] font-bold text-base">{selected.length}</span>
            )}
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-auto">
          <table className="w-full min-w-[640px] table-fixed border-collapse text-[12px]">
            <colgroup>
              {/* CALLS: θ IV Δ Mark OI — Mark widened for the B · price · S row */}
              <col className="w-[8%]" />
              <col className="w-[8%]" />
              <col className="w-[8%]" />
              <col className="w-[13%]" />
              <col className="w-[8%]" />
              <col className="w-[10%]" />{/* STRIKE */}
              {/* PUTS: OI Mark Δ IV θ */}
              <col className="w-[8%]" />
              <col className="w-[13%]" />
              <col className="w-[8%]" />
              <col className="w-[8%]" />
              <col className="w-[8%]" />
            </colgroup>
            <thead className="sticky top-0 z-10 bg-surface text-[10px] uppercase tracking-wider text-text-mute">
              <tr>
                <th colSpan={5} className="py-1 pl-3 text-left text-pos/70">CALLS</th>
                <th />
                <th colSpan={5} className="py-1 pr-3 text-right text-neg/70">PUTS</th>
              </tr>
              <tr>
                {["θ", "IV", "Δ", "Mark", "OI"].map((h) => (
                  <th key={`c${h}`} className="px-2 py-1.5 text-right font-medium">{h}</th>
                ))}
                <th className="px-3 py-1.5 text-center font-semibold text-text-dim">STRIKE</th>
                {["OI", "Mark", "Δ", "IV", "θ"].map((h) => (
                  <th key={`p${h}`} className="px-2 py-1.5 text-right font-medium">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {chain.rows.map((row, i) => {
                const atm = row.strike === chain.atmStrike;
                const callItm = row.strike < chain.spot;
                const putItm = row.strike > chain.spot;
                const next = chain.rows[i + 1];
                const spotHere = next && row.strike <= chain.spot && next.strike > chain.spot;
                const cs = row.call.symbol;
                const ps = row.put.symbol;
                const callHl = builderMode && sel.has(cs);
                const putHl = builderMode && sel.has(ps);
                const callSel = builderMode ? sel.get(cs) : undefined;
                const putSel = builderMode ? sel.get(ps) : undefined;
                // VIEW mode: tapping a cell focuses it and (mobile) opens the depth sheet.
                // BUILDER mode: tapping the MARK reveals B/S for that one cell (tap-to-reveal).
                const onView = (sym: string) => () => { setFocused(sym); if (!builderMode) setDepthOpen(true); };
                const onReveal = (sym: string) => () => setActiveCell((a) => (a === sym ? null : sym));
                const addLeg = (c: Contract, s: Side) => { selectLeg(c, s); setActiveCell(null); };
                const onCall = onView(cs);
                const onPut = onView(ps);
                return (
                  <Fragment key={row.strike}>
                    <tr className={clsx("group border-b border-line/40 hover:bg-white/[0.04]", atm && "bg-warn/[0.05]")}>
                      <Cell n={row.call.greeks.theta.toFixed(1)} hl={callHl} badge={callSel} badgeSide="left" onClick={onCall} />
                      <Cell n={(row.call.iv * 100).toFixed(1)} hl={callHl} onClick={onCall} />
                      <Cell n={row.call.greeks.delta.toFixed(2)} hl={callHl} onClick={onCall} />
                      <MarkCell c={row.call} itm={callItm} hl={callHl} pos={posMap.get(cs)} flash={flash.get(cs)} mode={builderMode} active={activeCell === cs} onReveal={onReveal(cs)} onView={onCall} onAdd={addLeg} align="right" />
                      <OiCell n={oiC(row.call)} max={maxCallOi} side="left" hl={callHl} onClick={onCall} />
                      <td ref={atm ? atmRef : undefined} className={clsx("px-3 text-center tnum font-semibold", atm ? "text-warn" : "text-text-dim")}>{row.strike}</td>
                      <OiCell n={oiC(row.put)} max={maxPutOi} side="right" hl={putHl} onClick={onPut} />
                      <MarkCell c={row.put} itm={putItm} hl={putHl} pos={posMap.get(ps)} flash={flash.get(ps)} mode={builderMode} active={activeCell === ps} onReveal={onReveal(ps)} onView={onPut} onAdd={addLeg} align="left" />
                      <Cell n={row.put.greeks.delta.toFixed(2)} hl={putHl} onClick={onPut} />
                      <Cell n={(row.put.iv * 100).toFixed(1)} hl={putHl} onClick={onPut} />
                      <Cell n={row.put.greeks.theta.toFixed(1)} hl={putHl} badge={putSel} badgeSide="right" onClick={onPut} />
                    </tr>
                    {spotHere && (
                      <tr aria-hidden>
                        <td colSpan={11} className="p-0">
                          <div className="flex items-center">
                            <div className="h-px flex-1 bg-accent/60" />
                            <span className="bg-accent px-2 py-0.5 text-[10px] font-bold tnum text-base">
                              {chain.underlying} {chain.spot.toLocaleString("en-IN", { maximumFractionDigits: 1 })}
                            </span>
                            <div className="h-px flex-1 bg-accent/60" />
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {/* Desktop (lg+): persistent side panel — builder XOR order book. */}
      <div className="hidden lg:flex">
        {builderMode ? (
          <StrategyBuilder onClose={() => setBuilderMode(false)} />
        ) : (
          <OrderBook symbol={focusedSym} mark={focusedMark} ltp={focusedLtp} />
        )}
      </div>

      {/* Mobile: builder mode → sticky "Done" bar → full-screen builder page.
          The chain keeps the full height; the basket lives on its own page. */}
      {builderMode && selected.length > 0 && (
        <div className="sticky bottom-0 z-30 flex items-center gap-3 border-t border-line bg-surface px-3 py-2 lg:hidden">
          <span className="text-[12px] font-medium text-text-dim">
            {selected.length} {selected.length === 1 ? "leg" : "legs"} added
          </span>
          <button onClick={clearLegs} className="text-[12px] text-text-mute hover:text-text-dim">
            Clear all
          </button>
          <button
            onClick={() => router.push("/chain/builder")}
            className="ml-auto rounded-[6px] bg-accent px-5 py-2 text-[13px] font-semibold text-base"
          >
            Done
          </button>
        </div>
      )}

      {/* Mobile: depth opens on demand as a bottom sheet (view mode, tap a mark). */}
      <MarketDepthSheet
        open={depthOpen && !builderMode}
        symbol={focusedSym}
        mark={focusedMark}
        ltp={focusedLtp}
        onClose={() => setDepthOpen(false)}
      />
    </div>
  );
}

// Greek cell — clickable to select its side; the outer θ cell carries the
// selected-leg B/S badge at the row end.
function Cell({
  n, hl, badge, badgeSide, onClick,
}: { n: string; hl?: boolean; badge?: Side; badgeSide?: "left" | "right"; onClick?: () => void }) {
  return (
    <td onClick={onClick} className={clsx("relative px-2 py-1.5 text-right tnum text-text-dim", onClick && "cursor-pointer", hl && HL)}>
      {badge && (
        <span className={clsx(
          "absolute top-1/2 -translate-y-1/2 rounded px-1 text-[9px] font-bold text-base",
          badgeSide === "left" ? "left-0.5" : "right-0.5",
          badge === "buy" ? "bg-pos" : "bg-neg",
        )}>
          {badge === "buy" ? "B" : "S"}
        </span>
      )}
      {n}
    </td>
  );
}

function OiCell({
  n, max, side, hl, onClick,
}: { n: number; max: number; side: "left" | "right"; hl?: boolean; onClick?: () => void }) {
  const pct = Math.min(100, (n / max) * 100);
  return (
    <td onClick={onClick} className={clsx("relative px-2 py-1.5 text-right tnum text-text-mute", onClick && "cursor-pointer", hl && HL)}>
      <span className="absolute inset-y-1 bg-info/15" style={{ width: `${pct}%`, [side === "left" ? "left" : "right"]: 0 } as React.CSSProperties} />
      <span className="relative">{compactOi(n)}</span>
    </td>
  );
}

// Mark = tradeable cell. VIEW mode: tap → focus + (mobile) open the depth sheet. BUILDER
// mode: tap → reveal B/S for THIS cell only (`active`); tap B/S to add. The B/S stay hidden
// (and non-interactive) until revealed — on desktop via hover, on mobile via the tap — so the
// chain isn't littered with B/S on every row. `pos` shows L/S for an open position.
function MarkCell({
  c, itm, hl, pos, flash, mode, active, onReveal, onView, onAdd, align,
}: {
  c: Contract; itm: boolean; hl?: boolean; pos?: Side; flash?: "up" | "down";
  mode: boolean; active: boolean; onReveal: () => void; onView: () => void;
  onAdd: (c: Contract, s: Side) => void; align: "left" | "right";
}) {
  // visible when this cell is the tapped one (mobile) OR the row is hovered (desktop)
  const reveal = active
    ? "opacity-100"
    : "pointer-events-none opacity-0 group-hover:pointer-events-auto group-hover:opacity-100";
  return (
    <td
      onClick={mode ? onReveal : onView}
      title={mode ? "tap to add" : "view depth"}
      className={clsx(
        "relative cursor-pointer px-2 py-1.5 tnum font-semibold",
        itm ? "text-text" : "text-text-dim",
        !mode && "hover:text-accent",
        flash === "up" && "flash-up",
        flash === "down" && "flash-down",
        hl && HL,
      )}
    >
      {pos && (
        <span className={clsx(
          "absolute top-1/2 z-10 -translate-y-1/2 rounded px-1 text-[9px] font-bold text-base",
          align === "right" ? "left-0.5" : "right-0.5",
          pos === "buy" ? "bg-pos" : "bg-neg",
        )}>
          {pos === "buy" ? "L" : "S"}
        </span>
      )}
      <div className="flex items-center justify-center gap-1.5">
        {mode && (
          <button
            aria-label={`Buy ${c.symbol}`}
            onClick={(e) => { e.stopPropagation(); onAdd(c, "buy"); }}
            className={clsx("rounded bg-pos px-2 py-1 text-[12px] font-bold leading-none text-base transition-opacity touch-manipulation hover:opacity-90", reveal)}
          >
            B
          </button>
        )}
        <span>{c.mark.toFixed(1)}</span>
        {mode && (
          <button
            aria-label={`Sell ${c.symbol}`}
            onClick={(e) => { e.stopPropagation(); onAdd(c, "sell"); }}
            className={clsx("rounded bg-neg px-2 py-1 text-[12px] font-bold leading-none text-base transition-opacity touch-manipulation hover:opacity-90", reveal)}
          >
            S
          </button>
        )}
      </div>
    </td>
  );
}
