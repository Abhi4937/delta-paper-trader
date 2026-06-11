"use client";

import clsx from "clsx";
import { useEffect, useState } from "react";
import { type BookLevel, fetchOrderbook } from "@/lib/api";

// L2 depth ladder for a focused contract (Delta-style: asks on top, mark in the
// middle with best bid/ask + spread, bids below — depth bars behind each row).
export default function OrderBook({
  symbol, mark, ltp,
}: { symbol: string | null; mark: number | null; ltp: number | null }) {
  const [book, setBook] = useState<{ buy: BookLevel[]; sell: BookLevel[] }>({ buy: [], sell: [] });

  useEffect(() => {
    if (!symbol) return;
    let on = true;
    const tick = async () => {
      const b = await fetchOrderbook(symbol);
      if (on) setBook(b);
    };
    tick();
    const h = setInterval(tick, 700);
    return () => {
      on = false;
      clearInterval(h);
    };
  }, [symbol]);

  const asks = [...book.sell].sort((a, b) => a.price - b.price).slice(0, 12).reverse();
  const bids = [...book.buy].sort((a, b) => b.price - a.price).slice(0, 12);
  const maxSize = Math.max(1, ...asks.map((l) => l.size), ...bids.map((l) => l.size));
  const bestBid = bids[0]?.price ?? null;
  const bestAsk = asks[asks.length - 1]?.price ?? null;
  const spread = bestBid != null && bestAsk != null ? bestAsk - bestBid : null;

  return (
    <aside className="flex max-h-[55vh] w-full shrink-0 flex-col border-t border-line bg-surface lg:max-h-none lg:w-[420px] lg:border-l lg:border-t-0">
      <div className="border-b border-line px-3 py-2.5">
        <div className="text-[13px] font-semibold text-text">Order Book</div>
        <div className="tnum text-[10px] text-text-mute">{symbol ?? "—"}</div>
      </div>

      {!symbol ? (
        <div className="grid flex-1 place-items-center px-4 text-center text-[11px] text-text-mute">
          click a strike’s mark to view its depth
        </div>
      ) : (
        <div className="flex min-h-0 flex-1 flex-col text-[12px]">
          <div className="grid grid-cols-2 px-3 py-1 text-[9px] uppercase tracking-wider text-text-mute">
            <span>Price</span>
            <span className="text-right">Size (BTC)</span>
          </div>
          {/* asks */}
          <div className="flex flex-1 flex-col justify-end overflow-hidden">
            {asks.map((l, i) => (
              <Row key={`a${i}`} level={l} max={maxSize} tone="neg" />
            ))}
            {asks.length === 0 && <Empty />}
          </div>

          {/* mark + best bid/ask + spread */}
          <div className="border-y border-line bg-surface-2 px-3 py-2">
            <div className="flex items-baseline justify-between">
              <span className="flex items-baseline gap-1.5">
                <span className="text-[10px] uppercase tracking-wider text-text-mute">Mark</span>
                <span className="tnum text-[16px] font-bold text-text">{mark != null ? mark.toFixed(1) : "—"}</span>
              </span>
              <span className="flex items-baseline gap-1.5">
                <span className="text-[10px] uppercase tracking-wider text-text-mute">LTP</span>
                <span className="tnum text-[13px] font-semibold text-text-dim">{ltp != null ? ltp.toFixed(1) : "—"}</span>
              </span>
            </div>
            <div className="mt-1 flex items-center justify-between text-[10px]">
              <span className="text-text-mute">
                Bid <span className="tnum text-pos">{bestBid != null ? bestBid.toFixed(1) : "—"}</span>
              </span>
              <span className="text-text-mute">
                Spread <span className="tnum text-text-dim">{spread != null ? spread.toFixed(1) : "—"}</span>
              </span>
              <span className="text-text-mute">
                Ask <span className="tnum text-neg">{bestAsk != null ? bestAsk.toFixed(1) : "—"}</span>
              </span>
            </div>
          </div>

          {/* bids */}
          <div className="flex flex-1 flex-col overflow-hidden">
            {bids.map((l, i) => (
              <Row key={`b${i}`} level={l} max={maxSize} tone="pos" />
            ))}
            {bids.length === 0 && <Empty />}
          </div>
        </div>
      )}
    </aside>
  );
}

function Empty() {
  return <div className="grid flex-1 place-items-center text-[11px] text-text-mute">no depth</div>;
}

function Row({ level, max, tone }: { level: BookLevel; max: number; tone: "pos" | "neg" }) {
  const pct = Math.min(100, (level.size / max) * 100);
  return (
    <div className="relative grid grid-cols-2 px-3 py-[3px] tnum">
      <span
        className={clsx("absolute inset-y-0 right-0", tone === "pos" ? "bg-pos/12" : "bg-neg/12")}
        style={{ width: `${pct}%` }}
      />
      <span className={clsx("relative", tone === "pos" ? "text-pos" : "text-neg")}>
        {level.price.toFixed(1)}
      </span>
      <span className="relative text-right text-text-dim">{level.size}</span>
    </div>
  );
}
