"use client";

import { useState } from "react";
import clsx from "clsx";
import { RefreshCw } from "lucide-react";
import PositionCard from "@/components/PositionCard";
import { money, positionPnl, useStore } from "@/lib/store";

export default function PositionsPage() {
  // subscribe to positions + tickN so live marks re-render the PnL
  // live (real Delta) groups have their own page — this one is paper only
  const all = useStore((s) => s.positions);
  const positions = all.filter((p) => p.source !== "live");
  const currency = useStore((s) => s.currency);
  useStore((s) => s.tickN);
  const closePosition = useStore((s) => s.closePosition);
  const hydrated = useStore((s) => s.hydrated);
  // optimistic hint: did this user have open positions last session? → show "Loading…"
  // (not "No positions") until the first server fetch returns.
  const [hadPositions] = useState(() => {
    try { return localStorage.getItem("pt:hadOpenPositions") === "1"; } catch { return false; }
  });

  const open = positions.filter((p) => p.status === "open");
  const totalUpnl = open.reduce((s, p) => s + positionPnl(p), 0);
  const totalMargin = open.reduce((s, p) => s + p.margin, 0);
  // open positions first (newest entry on top), then all closed beneath (most recently
  // closed first) — so the live book stays at the top and history sits below it.
  const ordered = [...positions].sort((a, b) => {
    const ao = a.status === "open" ? 0 : 1;
    const bo = b.status === "open" ? 0 : 1;
    if (ao !== bo) return ao - bo;
    return ao === 0 ? b.openedAt - a.openedAt : (b.closedAt ?? 0) - (a.closedAt ?? 0);
  });

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-line px-4 py-2.5">
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
        !hydrated && hadPositions ? (
          <div className="grid flex-1 place-items-center text-[13px] text-text-mute">
            <div className="flex items-center gap-2"><RefreshCw size={13} className="animate-spin" /> Loading your positions…</div>
          </div>
        ) : (
          <div className="grid flex-1 place-items-center text-center text-[13px] text-text-mute">
            <div>
              No positions yet.
              <div className="mt-1 text-[11px]">Build a strategy on the chain and Place Paper Order.</div>
            </div>
          </div>
        )
      ) : (
        <div className="min-h-0 flex-1 space-y-3 overflow-auto p-4">
          {ordered.map((p) => (
            <PositionCard key={p.id} p={p} currency={currency} onClose={closePosition} />
          ))}
        </div>
      )}
    </div>
  );
}

