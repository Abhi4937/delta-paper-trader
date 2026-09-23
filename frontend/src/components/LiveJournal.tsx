"use client";

import clsx from "clsx";
import { Download } from "lucide-react";
import { useEffect, useState } from "react";
import { type LiveJournal as Journal, fetchLiveJournal } from "@/lib/api";
import { exportLiveJournalXlsx } from "@/lib/exportXlsx";
import { type Currency, money } from "@/lib/store";

const fmt = (ms: number | null) =>
  ms
    ? new Date(ms).toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" })
    : "—";
const dur = (s: number) => (s >= 3600 ? `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m` : `${Math.floor(s / 60)}m`);

// Live trade journal: one row per live trade (snapshot frozen at close), the live-only log,
// and an Excel download of both. Kept apart from the paper Logs page.
export default function LiveJournal({ currency }: { currency: Currency }) {
  const [j, setJ] = useState<Journal | null>(null);
  const [showLog, setShowLog] = useState(false);

  useEffect(() => {
    const load = () => fetchLiveJournal().then((r) => r && setJ(r));
    load();
    const id = setInterval(load, 30000);
    return () => clearInterval(id);
  }, []);

  const pnl = (v: number | null) => (
    <span className={clsx("tnum", v == null ? "text-text-mute" : v >= 0 ? "text-pos" : "text-neg")}>
      {v == null ? "—" : `${v >= 0 ? "+" : ""}${money(v, currency)}`}
    </span>
  );

  return (
    <div className="rounded-lg border border-line bg-surface">
      <div className="flex items-center gap-3 border-b border-line/60 px-4 py-2.5">
        <span className="text-[13px] font-semibold text-text">Live trade journal</span>
        <span className="text-[11px] text-text-mute">{j ? `${j.trades.length} trades` : "loading…"}</span>
        <button
          onClick={() => setShowLog((v) => !v)}
          className="ml-auto rounded-[5px] border border-line px-2 py-1 text-[11px] text-text-mute hover:text-text"
        >
          {showLog ? "Hide" : "Show"} live log{j ? ` · ${j.logs.length}` : ""}
        </button>
        <button
          onClick={() => j && exportLiveJournalXlsx(j)}
          disabled={!j}
          className="flex items-center gap-1 rounded-[5px] border border-line px-2 py-1 text-[11px] text-text-mute hover:text-text disabled:opacity-50"
          title="Trades + every leg + the live log, as Excel"
        >
          <Download size={12} /> Download journal
        </button>
      </div>

      <div className="overflow-x-auto px-4 py-1">
        <div className="grid min-w-[900px] grid-cols-[minmax(140px,1.2fr)_100px_100px_60px_90px_110px_110px_90px_minmax(120px,1fr)] gap-2 py-1 text-[9px] uppercase tracking-wider text-text-mute">
          <span>Trade</span><span>Opened</span><span>Closed</span><span>Time</span><span className="text-right">P&amp;L</span>
          <span className="text-right">Max MTM</span><span className="text-right">Min MTM</span>
          <span className="text-right">Max DD</span><span>Close reason</span>
        </div>
        {j?.trades.length === 0 && <div className="py-3 text-[11px] text-text-mute">No live trades yet.</div>}
        {j?.trades.map((r) => (
          <div key={r.id} className="grid min-w-[900px] grid-cols-[minmax(140px,1.2fr)_100px_100px_60px_90px_110px_110px_90px_minmax(120px,1fr)] items-center gap-2 border-t border-line/40 py-1 text-[11px]">
            <span className="truncate font-medium">
              {r.name}
              {r.status === "open" && <span className="ml-1 rounded-[3px] bg-accent/15 px-1 text-[8px] uppercase text-accent">open</span>}
            </span>
            <span className="tnum text-[10px] text-text-mute">{fmt(r.openedAt)}</span>
            <span className="tnum text-[10px] text-text-mute">{fmt(r.closedAt)}</span>
            <span className="tnum text-[10px] text-text-mute">{dur(r.durationSeconds)}</span>
            <span className="text-right">{pnl(r.pnl)}</span>
            <span className="text-right" title={fmt(r.maxMtmAt)}>{pnl(r.maxMtm)}</span>
            <span className="text-right" title={fmt(r.minMtmAt)}>{pnl(r.minMtm)}</span>
            <span className="tnum text-right text-neg" title={`peak ${fmt(r.drawdownPeakAt)} → trough ${fmt(r.drawdownTroughAt)}`}>
              {r.maxDrawdown ? `-${money(r.maxDrawdown, currency)}` : "—"}
            </span>
            <span className="truncate text-[10px] text-text-dim">{r.closeReason ?? "—"}</span>
          </div>
        ))}
      </div>

      {showLog && j && (
        <div className="max-h-72 overflow-auto border-t border-line/60 px-4 py-2">
          {j.logs.length === 0 && <div className="text-[11px] text-text-mute">No live log entries yet.</div>}
          {j.logs.map((l, i) => (
            <div key={i} className="flex gap-3 border-t border-line/30 py-0.5 text-[10px] first:border-t-0">
              <span className="tnum w-24 flex-none text-text-mute">{fmt(l.t)}</span>
              <span className={clsx("w-24 flex-none font-medium", l.tone === "warn" ? "text-warn" : "text-text-dim")}>{l.action}</span>
              <span className="min-w-0 text-text-dim">{l.detail}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
