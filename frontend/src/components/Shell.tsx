"use client";

import clsx from "clsx";
import {
  LayoutGrid,
  LineChart,
  NotebookPen,
  ReceiptText,
  Settings,
  Wallet,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect } from "react";
import { type Currency, money, startStream, useStore } from "@/lib/store";
import type { Underlying } from "@/lib/types";

const NAV = [
  { href: "/chain", label: "Chain & Builder", icon: LayoutGrid },
  { href: "/positions", label: "Positions", icon: Wallet },
  { href: "/analytics", label: "Analytics", icon: LineChart },
  { href: "/notes", label: "Notes", icon: NotebookPen },
  { href: "/logs", label: "Logs", icon: ReceiptText },
];

function expLabel(iso: string): string {
  const d = new Date(iso + "T00:00:00Z");
  return `${d.getUTCDate()}${d.toLocaleString("en", { month: "short", timeZone: "UTC" })}`;
}

export default function Shell({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const { underlying, expiry, expiries, chain, balance, tickN, conn, currency } = useStore();
  const setU = useStore((s) => s.setUnderlying);
  const setE = useStore((s) => s.setExpiry);
  const setCurrency = useStore((s) => s.setCurrency);

  useEffect(() => startStream(), []);

  const connColor = conn === "live" ? "bg-accent" : conn === "down" ? "bg-neg" : "bg-warn";

  return (
    <div className="grid h-dvh grid-cols-[60px_1fr] grid-rows-[52px_1fr] overflow-hidden">
      <div className="flex items-center justify-center border-b border-r border-line">
        <div className="grid h-8 w-8 place-items-center rounded-[5px] bg-accent text-[13px] font-bold text-base">
          Δ
        </div>
      </div>

      <header className="flex items-center gap-3 border-b border-line px-4">
        <div className="flex items-center gap-1">
          {(["BTC", "ETH"] as Underlying[]).map((u) => (
            <button
              key={u}
              onClick={() => setU(u)}
              className={clsx(
                "rounded-[5px] px-2.5 py-1 text-[12px] font-semibold tracking-wide transition-colors",
                underlying === u ? "bg-surface-3 text-text" : "text-text-mute hover:text-text-dim",
              )}
            >
              {u}
            </button>
          ))}
        </div>
        <div className="h-4 w-px bg-line" />
        <div className="flex items-center gap-0.5 overflow-x-auto">
          {expiries.map((e) => (
            <button
              key={e}
              onClick={() => setE(e)}
              className={clsx(
                "tnum whitespace-nowrap rounded-[5px] px-2 py-1 text-[11px] transition-colors",
                expiry === e ? "bg-surface-3 text-text" : "text-text-mute hover:text-text-dim",
              )}
            >
              {expLabel(e)}
            </button>
          ))}
        </div>

        <div className="ml-2 flex items-baseline gap-2">
          <span className="text-[10px] uppercase tracking-wider text-text-mute">{underlying}</span>
          <span className="tnum text-[15px] font-semibold text-text">
            {chain ? chain.spot.toLocaleString("en-IN", { maximumFractionDigits: 1 }) : "—"}
          </span>
        </div>

        <div className="ml-auto flex items-center gap-3">
          {/* currency toggle ($/₹ fixed 85) */}
          <div className="flex overflow-hidden rounded-[5px] border border-line text-[12px]">
            {(["USD", "INR"] as Currency[]).map((c) => (
              <button
                key={c}
                onClick={() => setCurrency(c)}
                className={clsx(
                  "px-2 py-1 font-semibold transition-colors",
                  currency === c ? "bg-surface-3 text-text" : "text-text-mute hover:text-text-dim",
                )}
              >
                {c === "USD" ? "$" : "₹"}
              </button>
            ))}
          </div>
          <div className="text-right leading-tight">
            <div className="text-[10px] uppercase tracking-wider text-text-mute">Paper balance</div>
            <div className="tnum text-[13px] font-semibold text-accent">{money(balance, currency)}</div>
          </div>
          <div className="flex items-center gap-1.5 rounded-[5px] border border-line bg-surface px-2 py-1">
            <span className={clsx("h-1.5 w-1.5 rounded-full", connColor, conn === "live" && "live-dot")} />
            <span className="text-[11px] text-text-dim">{conn === "live" ? "live" : conn}</span>
            <span className="tnum text-[11px] text-text-mute">·{tickN}</span>
          </div>
          <Settings size={16} className="text-text-mute hover:text-text-dim" />
        </div>
      </header>

      <nav className="flex flex-col items-center gap-1 border-r border-line py-3">
        {NAV.map(({ href, label, icon: Icon }) => {
          const active = path === href;
          return (
            <Link
              key={href}
              href={href}
              title={label}
              className={clsx(
                "group relative grid h-10 w-10 place-items-center rounded-[6px] transition-colors",
                active ? "bg-surface-3 text-accent" : "text-text-mute hover:bg-surface hover:text-text-dim",
              )}
            >
              <Icon size={18} strokeWidth={active ? 2.2 : 1.8} />
              {active && <span className="absolute left-0 top-1/2 h-5 w-[2px] -translate-y-1/2 rounded-r bg-accent" />}
            </Link>
          );
        })}
      </nav>

      <main className="overflow-hidden">{children}</main>
    </div>
  );
}
