"use client";

import clsx from "clsx";
import { Minus, Plus, RefreshCw, X } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import PayoffPanel from "@/components/PayoffPanel";
import { fetchMargin } from "@/lib/api";
import { legBasketPrice, money, moneyBoth, useStore } from "@/lib/store";
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
  useStore((s) => s.tickN); // re-render on each chain tick so the live preview moves
  const router = useRouter();

  const [tab, setTab] = useState<"basket" | "payoff">("basket");
  const [margin, setMargin] = useState<{ value: number; badge: string; localMargin: number | null; divergence: number | null; calibrationFactor: number | null } | null>(null);
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
        setMargin(r ? { value: r.margin, badge: r.badge, localMargin: r.localMargin, divergence: r.divergence, calibrationFactor: r.calibrationFactor } : null);
        setLoadingM(false);
      }
    }, 350);
    return () => {
      cancelled = true;
      clearTimeout(t);
    };
  }, [key, underlying, selected]);

  // net credit/debit on the LIVE would-fill premium (moves in real time until execute)
  const net = selected.reduce(
    (s, l) => s + (l.side === "sell" ? 1 : -1) * l.qty * legBasketPrice(l) * l.contractValue,
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
    <aside className="flex max-h-[55vh] w-full shrink-0 flex-col border-t border-line bg-surface lg:max-h-none lg:w-[420px] lg:border-l lg:border-t-0">
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
                        Market Price <span className="tnum">· ${legBasketPrice(l).toFixed(1)}</span>
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
            <PayoffPanel />
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
            {/* surface BOTH: the exact Delta margin and the local estimate */}
            {margin && margin.badge === "matched" && margin.localMargin != null && (
              <div className="mb-1 flex items-center justify-between text-[10px] text-text-mute">
                <span>Local estimate</span>
                <span className="tnum">
                  {moneyBoth(margin.localMargin, currency)}
                  {margin.divergence != null && ` · ${margin.divergence >= 0 ? "+" : ""}${margin.divergence.toFixed(1)}% vs exact`}
                </span>
              </div>
            )}
            {margin && margin.badge !== "matched" && (
              <div className="mb-1 flex items-center justify-between text-[10px] text-text-mute">
                <span>Exact (Delta)</span>
                <span className="tnum">
                  unavailable
                  {margin.calibrationFactor && Math.abs(margin.calibrationFactor - 1) > 0.005
                    ? ` · est calibrated ×${margin.calibrationFactor.toFixed(3)}`
                    : ` · ${margin.badge === "stale" ? "token expired" : "no token"}`}
                </span>
              </div>
            )}
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
