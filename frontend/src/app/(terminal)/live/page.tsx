"use client";

import { Check, Lock, Radio } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import LiveJournal from "@/components/LiveJournal";
import PositionCard from "@/components/PositionCard";
import { type KeyStatus, type LiveStatus, fetchKeys, fetchLiveStatus } from "@/lib/api";
import { ensureStepUp, hasTotp } from "@/lib/auth";
import { useStore } from "@/lib/store";

export default function LivePage() {
  const [mfa, setMfa] = useState<boolean | null>(null);
  const [keys, setKeys] = useState<KeyStatus | null>(null);

  useEffect(() => {
    hasTotp().then(async (has) => {
      if (!has) setMfa(false);
      else if (await ensureStepUp("/live")) setMfa(true); // else: redirecting, stay loading
    });
    fetchKeys().then(setKeys);
  }, []);

  const loading = mfa === null || keys === null;
  const keysSet = !!keys && !!keys.delta_trade_key && !!keys.delta_trade_secret;
  const ready = mfa === true && keysSet;

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-3 border-b border-line px-4 py-2.5">
        <h1 className="text-[13px] font-semibold tracking-tight text-text">Live monitoring</h1>
        <span className="text-[11px] text-text-mute">Monitor & risk-manage your real Delta positions.</span>
      </div>

      <div className="min-h-0 flex-1 overflow-auto p-4">
        {loading ? (
          <div className="grid h-full place-items-center text-[12px] text-text-mute">Loading…</div>
        ) : ready ? (
          <LiveBook />
        ) : (
          <div className="mx-auto max-w-lg space-y-3">
            <p className="text-[12px] text-text-mute">
              Two steps to enable live monitoring of your real Delta account:
            </p>

            <Step
              n={1}
              done={mfa === true}
              locked={false}
              title="Set up two-factor authentication"
              desc="Live features handle real money, so they require an authenticator app."
              cta={mfa ? null : { href: "/enroll-2fa", label: "Set up 2FA" }}
            />
            <Step
              n={2}
              done={keysSet}
              locked={mfa !== true}
              title="Add your Delta trade API key"
              desc="Stored encrypted. Used only to read your positions and place reduce-only closes."
              cta={mfa && !keysSet ? { href: "/settings/keys", label: "Add keys" } : null}
            />
          </div>
        )}
      </div>
    </div>
  );
}

function Step({
  n, done, locked, title, desc, cta,
}: {
  n: number; done: boolean; locked: boolean; title: string; desc: string;
  cta: { href: string; label: string } | null;
}) {
  return (
    <div className={`flex items-start gap-3 rounded-lg border p-3 ${done ? "border-pos/30 bg-pos/5" : locked ? "border-line bg-surface-2 opacity-60" : "border-line bg-surface-2"}`}>
      <div className={`mt-0.5 grid h-6 w-6 flex-none place-items-center rounded-full text-[11px] font-semibold ${done ? "bg-pos/20 text-pos" : "bg-surface-3 text-text-dim"}`}>
        {done ? <Check size={14} /> : locked ? <Lock size={12} /> : n}
      </div>
      <div className="min-w-0 flex-1">
        <div className="text-[12px] font-medium text-text">{title}</div>
        <p className="text-[11px] text-text-mute">{desc}</p>
      </div>
      {cta && (
        <Link href={cta.href} className="flex-none rounded-[6px] bg-accent px-3 py-1.5 text-[11px] font-semibold text-base">
          {cta.label}
        </Link>
      )}
      {done && <span className="flex-none text-[10px] font-medium text-pos">done</span>}
    </div>
  );
}

// Real Delta positions, auto-grouped by underlying + expiry. Tracking only until armed.
function LiveBook() {
  const positions = useStore((s) => s.positions);
  const currency = useStore((s) => s.currency);
  useStore((s) => s.tickN);
  const [status, setStatus] = useState<LiveStatus | null>(null);

  const refresh = useCallback(() => {
    fetchLiveStatus().then((st) => st && setStatus(st));
  }, []);
  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 5000); // liquidation checks refresh every ~10s server-side
    return () => clearInterval(t);
  }, [refresh]);

  const live = positions
    .filter((p) => p.source === "live")
    .sort((a, b) => (a.status === b.status ? b.openedAt - a.openedAt : a.status === "open" ? -1 : 1));

  return (
    <div className="space-y-3">
      {status && !status.tradingEnabled && (
        <div className="rounded-lg border border-warn/40 bg-warn/10 px-3 py-2 text-[11px] text-warn">
          Live orders are switched OFF on the server (LIVE_TRADING_ENABLED=false). Positions are tracked
          and SLs evaluated, but no stop or close is sent to Delta — hits are only journaled.
        </div>
      )}
      {live.length === 0 ? (
        <div className="grid h-40 place-items-center text-center text-[12px] text-text-mute">
          <div>
            <Radio size={22} className="mx-auto mb-2 text-accent" />
            No open option positions on your Delta account yet.
            <div className="mt-1 text-[11px]">Trades you place on Delta appear here within a few seconds.</div>
          </div>
        </div>
      ) : (
        live.map((p) => (
          <PositionCard
            key={p.id}
            p={p}
            currency={currency}
            live={{ check: status?.checks[p.id], tradingEnabled: !!status?.tradingEnabled, onChanged: refresh }}
          />
        ))
      )}
      <LiveJournal currency={currency} />
    </div>
  );
}
