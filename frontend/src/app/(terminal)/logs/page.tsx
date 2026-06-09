"use client";

import { useState } from "react";
import clsx from "clsx";
import { money, useStore } from "@/lib/store";

const fmtT = (t: number) =>
  new Date(t).toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", second: "2-digit" });

const TONE: Record<string, string> = { pos: "text-pos", neg: "text-neg", warn: "text-warn", info: "text-info" };
const LEDGER_TONE: Record<string, string> = {
  deposit: "text-info", margin_reserve: "text-warn", margin_release: "text-text-dim", realized: "text-text", fee: "text-neg",
};

export default function LogsPage() {
  const logs = useStore((s) => s.logs);
  const ledger = useStore((s) => s.ledger);
  const currency = useStore((s) => s.currency);
  const [tab, setTab] = useState<"activity" | "ledger">("activity");

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-4 border-b border-line px-4 py-2.5">
        <h1 className="text-[13px] font-semibold tracking-tight text-text">Logs</h1>
        <div className="flex gap-1 rounded-[5px] bg-surface-2 p-0.5 text-[11px]">
          {([["activity", `Activity · ${logs.length}`], ["ledger", `Ledger · ${ledger.length}`]] as const).map(([t, l]) => (
            <button key={t} onClick={() => setTab(t)} className={clsx("rounded-[4px] px-2.5 py-1 font-medium transition-colors", tab === t ? "bg-surface-3 text-text" : "text-text-mute hover:text-text-dim")}>
              {l}
            </button>
          ))}
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-auto p-4">
        {tab === "activity" ? (
          logs.length === 0 ? (
            <Empty>No activity yet.</Empty>
          ) : (
            <div className="space-y-px text-[12px]">
              {logs.map((l, i) => (
                <div key={i} className="grid grid-cols-[140px_130px_1fr] items-baseline gap-3 rounded-[4px] px-2 py-1.5 hover:bg-surface">
                  <span className="tnum text-[11px] text-text-mute">{fmtT(l.t)}</span>
                  <span className={clsx("text-[10px] font-semibold uppercase tracking-wider", l.tone ? TONE[l.tone] : "text-text-dim")}>{l.action}</span>
                  <span className="text-text-dim">{l.detail}</span>
                </div>
              ))}
            </div>
          )
        ) : ledger.length === 0 ? (
          <Empty>No ledger entries yet.</Empty>
        ) : (
          <div className="rounded-lg border border-line">
            <div className="grid grid-cols-[150px_130px_1fr_110px_120px] gap-3 border-b border-line px-3 py-1.5 text-[9px] uppercase tracking-wider text-text-mute">
              <span>Time</span><span>Type</span><span>Ref</span><span className="text-right">Amount</span><span className="text-right">Balance</span>
            </div>
            {ledger.map((e, i) => (
              <div key={i} className="grid grid-cols-[150px_130px_1fr_110px_120px] items-center gap-3 border-t border-line/40 px-3 py-1.5 text-[12px]">
                <span className="tnum text-[11px] text-text-mute">{fmtT(e.t)}</span>
                <span className={clsx("text-[10px] uppercase tracking-wide", LEDGER_TONE[e.type] ?? "text-text-dim")}>{e.type.replace("_", " ")}</span>
                <span className="truncate text-text-mute">{e.ref ?? "—"}</span>
                <span className={clsx("tnum text-right font-medium", e.amount >= 0 ? "text-pos" : "text-neg")}>{e.amount >= 0 ? "+" : ""}{money(e.amount, currency)}</span>
                <span className="tnum text-right text-text-dim">{money(e.balanceAfter, currency)}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return <div className="grid h-full place-items-center text-[13px] text-text-mute">{children}</div>;
}
