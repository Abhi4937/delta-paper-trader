"use client";

import clsx from "clsx";
import {
  LayoutGrid,
  LineChart,
  NotebookPen,
  Radio,
  ReceiptText,
  Settings,
  Shield,
  Wallet,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { type Me, fetchMe } from "@/lib/api";
import { type Currency, money, startStream, useStore } from "@/lib/store";
import type { Underlying } from "@/lib/types";
import UserMenu from "./UserMenu";

const NAV = [
  { href: "/chain", label: "Chain & Builder", icon: LayoutGrid },
  { href: "/positions", label: "Positions", icon: Wallet },
  { href: "/live", label: "Live monitoring", icon: Radio },
  { href: "/analytics", label: "Analytics", icon: LineChart },
  { href: "/notes", label: "Notes", icon: NotebookPen },
  { href: "/logs", label: "Logs", icon: ReceiptText },
  { href: "/settings/keys", label: "API Keys", icon: Settings },
];

function expLabel(iso: string): string {
  const d = new Date(iso + "T00:00:00Z");
  return `${d.getUTCDate()}${d.toLocaleString("en", { month: "short", timeZone: "UTC" })}`;
}

export default function Shell({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const { underlying, expiry, expiries, chain, balance, tickN, conn, currency, feedFresh, feedAge } = useStore();
  const setU = useStore((s) => s.setUnderlying);
  const setE = useStore((s) => s.setExpiry);
  const setCurrency = useStore((s) => s.setCurrency);
  const [me, setMe] = useState<Me | null>(null);

  useEffect(() => startStream(), []);
  useEffect(() => {
    fetchMe().then(setMe);
  }, []);

  const nav = me?.isAdmin ? [...NAV, { href: "/admin", label: "Admin", icon: Shield }] : NAV;

  // stale = the Delta feed (not the localhost socket) is frozen/disconnected
  const stale = !feedFresh;
  const dotColor = stale ? "bg-warn" : conn === "live" ? "bg-accent" : conn === "down" ? "bg-neg" : "bg-warn";
  const connLabel = stale ? "stale" : conn === "live" ? "live" : conn;

  return (
    <div className="flex h-dvh flex-col overflow-hidden">
      {stale && (
        <div className="flex items-center gap-2 border-b border-warn/40 bg-warn/10 px-4 py-1.5 text-[11px] font-medium text-warn">
          ⚠ Delta market-data feed is stale{feedAge != null ? ` (${Math.round(feedAge)}s)` : ""} — prices may be frozen. Order placement is blocked until the feed is live again.
        </div>
      )}
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden lg:grid lg:grid-cols-[60px_1fr] lg:grid-rows-[52px_1fr]">
      <div className="hidden border-b border-r border-line lg:col-start-1 lg:row-start-1 lg:flex lg:items-center lg:justify-center">
        <div className="grid h-8 w-8 place-items-center rounded-[5px] bg-accent text-[13px] font-bold text-base">
          Δ
        </div>
      </div>

      <header className="order-1 flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-line px-3 py-1.5 lg:order-none lg:col-start-2 lg:row-start-1 lg:px-4 lg:py-0">
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
        <div className="hidden h-4 w-px bg-line lg:block" />
        <div className="hidden items-center gap-0.5 overflow-x-auto lg:flex">
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
            <div className="text-[10px] uppercase tracking-wider text-text-mute">Wallet balance</div>
            <div className="tnum text-[13px] font-semibold text-accent">{money(balance, currency)}</div>
          </div>
          <div className={clsx("flex items-center gap-1.5 rounded-[5px] border px-2 py-1", stale ? "border-warn/50 bg-warn/10" : "border-line bg-surface")} title={stale ? "Delta feed frozen/disconnected — placement blocked" : "Delta feed live"}>
            <span className={clsx("h-1.5 w-1.5 rounded-full", dotColor, !stale && conn === "live" && "live-dot")} />
            <span className={clsx("text-[11px]", stale ? "font-medium text-warn" : "text-text-dim")}>{connLabel}</span>
            <span className="tnum hidden text-[11px] text-text-mute lg:inline">·{tickN}</span>
          </div>
          <div className="border-l border-line pl-3">
            <UserMenu me={me} />
          </div>
        </div>
      </header>

      <nav className="order-3 flex items-center justify-around gap-1 overflow-x-auto border-t border-line py-1.5 lg:order-none lg:col-start-1 lg:row-start-2 lg:flex-col lg:justify-start lg:overflow-visible lg:border-t-0 lg:border-r lg:py-3">
        {nav.map(({ href, label, icon: Icon }) => {
          const active = path === href;
          return (
            <Link
              key={href}
              href={href}
              title={label}
              className={clsx(
                "group relative grid h-10 w-10 flex-none place-items-center rounded-[6px] transition-colors",
                active ? "bg-surface-3 text-accent" : "text-text-mute hover:bg-surface hover:text-text-dim",
              )}
            >
              <Icon size={18} strokeWidth={active ? 2.2 : 1.8} />
              {/* active marker: top bar on the mobile tab-bar, left bar on the desktop rail */}
              {active && (
                <span className="absolute left-1/2 top-0 h-[2px] w-5 -translate-x-1/2 rounded-b bg-accent lg:left-0 lg:top-1/2 lg:h-5 lg:w-[2px] lg:-translate-x-0 lg:-translate-y-1/2 lg:rounded-r" />
              )}
            </Link>
          );
        })}
      </nav>

      <main className="order-2 min-h-0 flex-1 overflow-y-auto lg:order-none lg:col-start-2 lg:row-start-2 lg:flex-none lg:overflow-hidden">{children}</main>
      </div>
    </div>
  );
}
