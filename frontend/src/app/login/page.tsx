"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { supabase } from "@/lib/supabase";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // already signed in (incl. just-completed Google OAuth redirect) → route onward
  useEffect(() => {
    const route = async () => {
      const { data } = await supabase.auth.getSession();
      if (!data.session) return;
      router.replace("/chain");
    };
    route();
    const { data: sub } = supabase.auth.onAuthStateChange((_e, session) => {
      if (session) route();
    });
    return () => sub.subscription.unsubscribe();
  }, [router]);

  async function google() {
    setErr(null);
    const { error } = await supabase.auth.signInWithOAuth({
      provider: "google",
      options: { redirectTo: `${window.location.origin}/login` },
    });
    if (error) setErr(error.message);
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    setBusy(true);
    try {
      const { error } = await supabase.auth.signInWithPassword({ email, password });
      if (error) throw error;
      router.replace("/chain");
    } catch (e2) {
      setErr(e2 instanceof Error ? e2.message : "Sign in failed");
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

        <button type="button" onClick={google}
          className="flex w-full items-center justify-center gap-2 rounded-[6px] border border-line bg-surface py-2 text-[13px] font-medium text-text hover:bg-surface-3">
          <svg width="16" height="16" viewBox="0 0 48 48" aria-hidden>
            <path fill="#EA4335" d="M24 9.5c3.5 0 6.6 1.2 9.1 3.6l6.8-6.8C35.6 2.4 30.2 0 24 0 14.6 0 6.4 5.4 2.5 13.3l7.9 6.1C12.3 13.2 17.7 9.5 24 9.5z" />
            <path fill="#4285F4" d="M46.5 24.5c0-1.6-.1-3.1-.4-4.5H24v9h12.7c-.5 3-2.2 5.5-4.7 7.2l7.3 5.7c4.3-3.9 6.8-9.7 6.8-17.4z" />
            <path fill="#FBBC05" d="M10.4 28.6c-.5-1.5-.8-3-.8-4.6s.3-3.1.8-4.6l-7.9-6.1C.9 16.5 0 20.1 0 24s.9 7.5 2.5 10.7l7.9-6.1z" />
            <path fill="#34A853" d="M24 48c6.5 0 11.9-2.1 15.9-5.8l-7.3-5.7c-2 1.4-4.7 2.3-8.6 2.3-6.3 0-11.7-3.7-13.6-9.9l-7.9 6.1C6.4 42.6 14.6 48 24 48z" />
          </svg>
          Continue with Google
        </button>

        <div className="flex items-center gap-2 text-[10px] uppercase text-text-mute">
          <span className="h-px flex-1 bg-line" /> or email <span className="h-px flex-1 bg-line" />
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

        <button type="submit" disabled={busy}
          className="w-full rounded-[6px] bg-accent py-2 text-[13px] font-semibold text-base disabled:opacity-50">
          {busy ? "…" : "Sign in"}
        </button>

        <p className="text-center text-[10px] text-text-mute">Invite-only — accounts are created by the admin. Your email must be allowlisted.</p>
      </form>
    </div>
  );
}
