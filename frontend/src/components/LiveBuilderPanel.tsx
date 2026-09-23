"use client";

import clsx from "clsx";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { type LiveCheck, fetchLiveAccount, fetchMe, livePrecheck } from "@/lib/api";
import { type Currency, USDINR, money } from "@/lib/store";
import type { Leg } from "@/lib/types";

// Builder footer panel for users with a Delta key: real wallet margin left after this
// basket, and — for a planned basket SL — whether that SL fires before liquidation.
export default function LiveBuilderPanel({
  legs, requiredMargin, currency,
}: { legs: Leg[]; requiredMargin: number | null; currency: Currency }) {
  const path = usePathname();
  const [hasKeys, setHasKeys] = useState(false);
  const [acct, setAcct] = useState<{ balance: number; available: number } | null>(null);
  const [acctErr, setAcctErr] = useState<{ status: number; error: string } | null>(null);
  const [slText, setSlText] = useState("");
  const [slUsd, setSlUsd] = useState<number | null>(null);
  const [check, setCheck] = useState<LiveCheck | null>(null);
  const [checkErr, setCheckErr] = useState<string | null>(null);
  const rate = currency === "INR" ? USDINR : 1;

  useEffect(() => {
    fetchMe().then((m) => setHasKeys(!!m?.hasLiveKeys));
  }, []);

  useEffect(() => {
    if (!hasKeys) return;
    const load = () =>
      fetchLiveAccount().then((r) => {
        if (r.ok) { setAcct({ balance: r.balance, available: r.available }); setAcctErr(null); }
        else setAcctErr({ status: r.status, error: r.error });
      });
    load();
    const id = setInterval(load, 30000);
    return () => clearInterval(id);
  }, [hasKeys]);

  const legKey = legs.map((l) => `${l.symbol}:${l.side}:${l.qty}`).join(",");
  useEffect(() => {
    if (!hasKeys || legs.length === 0) return; // an empty basket just hides the result
    let cancelled = false;
    const t = setTimeout(async () => {
      const r = await livePrecheck(legs.map((l) => ({ symbol: l.symbol, side: l.side, qty: l.qty })), slUsd);
      if (cancelled) return;
      if (r.ok) { setCheck(r.data.check); setCheckErr(null); } else { setCheck(null); setCheckErr(r.error); }
    }, 700);
    return () => { cancelled = true; clearTimeout(t); };
    // legKey captures legs' identity; re-run only when the basket or the SL changes
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasKeys, legKey, slUsd]);

  if (!hasKeys) return null;

  if (acctErr?.status === 403) {
    return (
      <div className="mb-2 rounded-[5px] border border-line bg-surface-2 px-2.5 py-1.5 text-[11px] text-text-mute">
        Delta account hidden —{" "}
        <Link href={`/enroll-2fa?next=${encodeURIComponent(path)}`} className="text-accent underline">enter your 2FA code</Link>{" "}
        to see real margin left.
      </div>
    );
  }

  const left = acct && requiredMargin != null ? acct.available - requiredMargin : null;
  const tone = check?.verdict === "green" ? "text-pos" : check?.verdict === "yellow" ? "text-warn" : "text-neg";
  const spot = (v: number | null) => (v == null ? "beyond ±50%" : v.toLocaleString("en-US", { maximumFractionDigits: 0 }));
  const commitSl = () => {
    const n = slText.trim() === "" ? null : Number(slText);
    setSlUsd(n != null && Number.isFinite(n) && n > 0 ? n / rate : null);
  };

  return (
    <div className="mb-2 space-y-1 rounded-[5px] border border-accent/30 bg-accent/5 px-2.5 py-1.5 text-[11px]">
      <div className="text-[9px] uppercase tracking-wider text-accent">Your Delta account</div>
      {acctErr ? (
        <div className="text-neg">{acctErr.error}</div>
      ) : (
        <>
          <Row label="Available (Delta)" value={acct ? money(acct.available, currency) : "…"} />
          <Row label="This basket needs" value={requiredMargin != null ? money(requiredMargin, currency) : "—"} />
          <Row
            label="Left after placing"
            value={left != null ? money(left, currency) : "—"}
            cls={left != null && left < 0 ? "text-neg font-semibold" : "text-text"}
          />
          {left != null && left < 0 && <div className="text-neg">Not enough margin on Delta for this basket.</div>}
        </>
      )}
      <div className="flex items-center justify-between gap-2 pt-0.5">
        <span className="text-text-mute">Planned basket SL ({currency === "INR" ? "₹" : "$"} loss)</span>
        <input
          type="text"
          inputMode="decimal"
          value={slText}
          placeholder="e.g. 5000"
          onChange={(e) => setSlText(e.target.value.replace(/[^0-9.]/g, ""))}
          onBlur={commitSl}
          onKeyDown={(e) => { if (e.key === "Enter") e.currentTarget.blur(); }}
          className="tnum w-20 rounded bg-surface-2 px-1.5 py-0.5 text-right text-[11px] text-text outline-none focus:ring-1 focus:ring-accent"
        />
      </div>
      {legs.length > 0 && checkErr && <div className="text-text-mute">Liquidation check: {checkErr}</div>}
      {legs.length > 0 && check && (
        <div className="space-y-0.5">
          <div className={clsx("font-semibold", tone)}>
            {slUsd == null
              ? "Set a planned SL to check it against liquidation"
              : check.verdict === "green" && check.sl_spot == null ? "No liquidation within a ±50% move (SL not reached either)"
              : check.verdict === "green" ? "SL fires well before liquidation"
              : check.verdict === "yellow" ? "SL is close to liquidation — cut size or tighten SL"
              : "Liquidation can come BEFORE this SL — don't place as is"}
          </div>
          <div className="tnum text-[10px] text-text-mute">
            SL spot {spot(check.sl_spot)} · liquidation spot {spot(check.liq_spot)}
            {slUsd != null && ` · safe size ≤ ${check.max_scale.toFixed(2)}× this basket`}
          </div>
        </div>
      )}
    </div>
  );
}

function Row({ label, value, cls }: { label: string; value: string; cls?: string }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-text-mute">{label}</span>
      <span className={clsx("tnum", cls ?? "text-text-dim")}>{value}</span>
    </div>
  );
}
