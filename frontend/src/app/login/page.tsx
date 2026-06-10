"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { getAAL } from "@/lib/auth";
import { supabase } from "@/lib/supabase";

export default function LoginPage() {
  const router = useRouter();
  const [mode, setMode] = useState<"signin" | "signup">("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  // already signed in → bounce to the gate's destination
  useEffect(() => {
    supabase.auth.getSession().then(async ({ data }) => {
      if (data.session) {
        const aal = await getAAL();
        router.replace(aal.current === "aal2" ? "/chain" : "/enroll-2fa");
      }
    });
  }, [router]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    setMsg(null);
    setBusy(true);
    try {
      if (mode === "signup") {
        const { data, error } = await supabase.auth.signUp({ email, password });
        if (error) throw error;
        if (!data.session) {
          setMsg("Account created. Check your email to confirm, then sign in.");
          setMode("signin");
          return;
        }
      } else {
        const { error } = await supabase.auth.signInWithPassword({ email, password });
        if (error) throw error;
      }
      const aal = await getAAL();
      router.replace(aal.current === "aal2" ? "/chain" : "/enroll-2fa");
    } catch (e2) {
      setErr(e2 instanceof Error ? e2.message : "Authentication failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid h-dvh place-items-center bg-base px-4">
      <form onSubmit={submit} className="w-full max-w-sm space-y-4 rounded-xl border border-line bg-surface-2 p-6">
        <div className="flex items-center gap-2">
          <div className="grid h-7 w-7 place-items-center rounded-[5px] bg-accent text-[13px] font-bold text-base">Δ</div>
          <div className="text-[15px] font-semibold text-text">Delta Paper · Sign in</div>
        </div>

        <div>
          <label className="mb-1 block text-[11px] uppercase tracking-wider text-text-mute">Email</label>
          <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)}
            className="w-full rounded-[6px] border border-line bg-surface px-3 py-2 text-[13px] text-text outline-none focus:border-accent" />
        </div>
        <div>
          <label className="mb-1 block text-[11px] uppercase tracking-wider text-text-mute">Password</label>
          <input type="password" required value={password} onChange={(e) => setPassword(e.target.value)}
            className="w-full rounded-[6px] border border-line bg-surface px-3 py-2 text-[13px] text-text outline-none focus:border-accent" />
        </div>

        {err && <div className="rounded-[6px] bg-neg/15 px-3 py-2 text-[12px] text-neg">{err}</div>}
        {msg && <div className="rounded-[6px] bg-pos/15 px-3 py-2 text-[12px] text-pos">{msg}</div>}

        <button type="submit" disabled={busy}
          className="w-full rounded-[6px] bg-accent py-2 text-[13px] font-semibold text-base disabled:opacity-50">
          {busy ? "…" : mode === "signin" ? "Sign in" : "Create account"}
        </button>

        <button type="button" onClick={() => { setMode(mode === "signin" ? "signup" : "signin"); setErr(null); setMsg(null); }}
          className="w-full text-[12px] text-text-mute hover:text-text-dim">
          {mode === "signin" ? "Need an account? Sign up" : "Have an account? Sign in"}
        </button>
        <p className="text-center text-[10px] text-text-mute">Access is invite-only — your email must be allowlisted.</p>
      </form>
    </div>
  );
}
