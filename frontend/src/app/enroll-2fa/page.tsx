"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { getAAL, signOut } from "@/lib/auth";
import { supabase } from "@/lib/supabase";

type Phase = "loading" | "enroll" | "challenge";

export default function Enroll2FAPage() {
  const router = useRouter();
  const [phase, setPhase] = useState<Phase>("loading");
  const [factorId, setFactorId] = useState("");
  const [qr, setQr] = useState("");
  const [secret, setSecret] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const init = useCallback(async () => {
    const { data: s } = await supabase.auth.getSession();
    if (!s.session) {
      router.replace("/login");
      return;
    }
    const aal = await getAAL();
    if (aal.current === "aal2") {
      router.replace("/chain");
      return;
    }
    const { data: factors } = await supabase.auth.mfa.listFactors();
    const verified = factors?.totp?.find((f) => f.status === "verified");
    if (verified) {
      setFactorId(verified.id);
      setPhase("challenge");
      return;
    }
    // remove any half-finished factors, then enroll fresh
    for (const f of factors?.totp ?? []) {
      if (f.status !== "verified") await supabase.auth.mfa.unenroll({ factorId: f.id });
    }
    const { data, error } = await supabase.auth.mfa.enroll({ factorType: "totp" });
    if (error || !data) {
      setErr(error?.message ?? "Could not start 2FA enrollment");
      return;
    }
    setFactorId(data.id);
    setQr(data.totp.qr_code);
    setSecret(data.totp.secret);
    setPhase("enroll");
  }, [router]);

  useEffect(() => {
    // async enrollment bootstrap (fetch factors → enroll/challenge); state is set
    // only after awaits, not synchronously in the effect body.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    init();
  }, [init]);

  async function verify(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    setBusy(true);
    try {
      const { error } = await supabase.auth.mfa.challengeAndVerify({ factorId, code });
      if (error) throw error;
      router.replace("/chain");
    } catch (e2) {
      setErr(e2 instanceof Error ? e2.message : "Invalid code");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid h-dvh place-items-center bg-base px-4">
      <div className="w-full max-w-sm space-y-4 rounded-xl border border-line bg-surface-2 p-6">
        <div className="text-[15px] font-semibold text-text">
          {phase === "challenge" ? "Two-factor authentication" : "Set up two-factor auth"}
        </div>
        <p className="text-[12px] text-text-mute">
          {phase === "challenge"
            ? "Enter the 6-digit code from your authenticator app."
            : "Scan this QR with an authenticator app (Google Authenticator, Authy, 1Password), then enter the 6-digit code."}
        </p>

        {phase === "loading" && <div className="py-6 text-center text-[12px] text-text-mute">Loading…</div>}

        {phase === "enroll" && qr && (
          <div className="flex flex-col items-center gap-2">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={qr} alt="TOTP QR code" className="h-44 w-44 rounded bg-white p-1" />
            <div className="text-[10px] text-text-mute">
              Or enter manually: <span className="tnum break-all text-text-dim">{secret}</span>
            </div>
          </div>
        )}

        {phase !== "loading" && (
          <form onSubmit={verify} className="space-y-3">
            <input
              inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]*" maxLength={6}
              placeholder="123456" value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
              className="tnum w-full rounded-[6px] border border-line bg-surface px-3 py-2 text-center text-[18px] tracking-[0.3em] text-text outline-none focus:border-accent"
            />
            {err && <div className="rounded-[6px] bg-neg/15 px-3 py-2 text-[12px] text-neg">{err}</div>}
            <button type="submit" disabled={busy || code.length !== 6}
              className="w-full rounded-[6px] bg-accent py-2 text-[13px] font-semibold text-base disabled:opacity-50">
              {busy ? "Verifying…" : "Verify"}
            </button>
          </form>
        )}

        <button onClick={() => signOut().then(() => router.replace("/login"))}
          className="w-full text-[11px] text-text-mute hover:text-text-dim">
          Sign out
        </button>
      </div>
    </div>
  );
}
