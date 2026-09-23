"use client";

import clsx from "clsx";
import { AlertTriangle } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef } from "react";
import type { LiveAlert } from "@/lib/api";
import { useStore } from "@/lib/store";

const RANK: Record<LiveAlert["level"], number> = { info: 0, warning: 1, critical: 2, emergency: 3 };

// Short beep via WebAudio — no asset to ship. Browsers may block it until the page has
// had a user gesture; the banner + notification still show.
function beep(urgent: boolean) {
  try {
    const ctx = new AudioContext();
    const osc = ctx.createOscillator();
    osc.frequency.value = urgent ? 880 : 660;
    osc.connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + (urgent ? 0.6 : 0.25));
  } catch { /* audio unavailable */ }
}

// Red banner on every page for live (real Delta) alerts. New or escalated warnings and
// above also beep and raise a browser notification (Telegram is sent server-side).
export default function LiveAlertBar() {
  const alerts = useStore((s) => s.liveAlerts);
  const seen = useRef<Record<string, number>>({});

  useEffect(() => {
    if (typeof Notification !== "undefined" && Notification.permission === "default" && alerts.length) {
      Notification.requestPermission().catch(() => {});
    }
    for (const a of alerts) {
      const r = RANK[a.level];
      if (r < 1 || (seen.current[a.key] ?? -1) >= r) continue;
      seen.current[a.key] = r;
      beep(r >= 2);
      try {
        if (typeof Notification !== "undefined" && Notification.permission === "granted") {
          new Notification(`Live ${a.level}`, { body: a.message, tag: a.key });
        }
      } catch { /* notifications unavailable */ }
    }
  }, [alerts]);

  const shown = alerts.filter((a) => RANK[a.level] >= 1);
  if (!shown.length) return null;
  const top = shown[0];
  const severe = RANK[top.level] >= 2;
  return (
    <Link
      href="/live"
      className={clsx(
        "flex items-start gap-2 border-b px-4 py-1.5 text-[11px]",
        severe ? "border-neg/50 bg-neg/15 text-neg" : "border-warn/40 bg-warn/10 text-warn",
      )}
    >
      <AlertTriangle size={13} className="mt-0.5 flex-none" />
      <span className="min-w-0 flex-1">
        <b className="uppercase">{top.level}</b> · {top.message}
        {shown.length > 1 && <span className="ml-2 opacity-80">(+{shown.length - 1} more)</span>}
      </span>
    </Link>
  );
}
