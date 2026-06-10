"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { fetchMe } from "@/lib/api";
import { getAAL, signOut } from "@/lib/auth";
import { supabase } from "@/lib/supabase";

// Client-side route guard for the terminal. Requires: 1) a Supabase session, 2) backend
// authorization (/api/me succeeds only if allowlisted), and 3) a 2FA-cleared (aal2)
// session IF the user holds live Delta trade keys. Paper-only users skip 2FA.
// A logged-in but non-allowlisted user gets a "not authorized" screen, never the app.
export default function AuthGate({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [state, setState] = useState<"checking" | "ok" | "unauthorized">("checking");

  useEffect(() => {
    let active = true;
    async function check() {
      const { data } = await supabase.auth.getSession();
      if (!data.session) {
        router.replace("/login");
        return;
      }
      const me = await fetchMe(); // null if the backend rejects (not allowlisted)
      if (!me) {
        if (active) setState("unauthorized");
        return;
      }
      // Live-key holders must clear 2FA every session; paper-only users don't.
      if (me.hasLiveKeys) {
        const aal = await getAAL();
        if (aal.current !== "aal2") {
          router.replace("/enroll-2fa");
          return;
        }
      }
      if (active) setState("ok");
    }
    check();
    const { data: sub } = supabase.auth.onAuthStateChange((_e, session) => {
      if (!session) router.replace("/login");
    });
    return () => {
      active = false;
      sub.subscription.unsubscribe();
    };
  }, [router]);

  if (state === "unauthorized")
    return (
      <div className="grid h-dvh place-items-center bg-base px-4">
        <div className="max-w-sm space-y-3 rounded-xl border border-line bg-surface-2 p-6 text-center">
          <div className="text-[15px] font-semibold text-text">Not authorized</div>
          <p className="text-[12px] text-text-mute">
            Your account isn&apos;t on the allowlist yet. Ask the admin to add your email, then sign in again.
          </p>
          <button
            onClick={() => signOut().then(() => router.replace("/login"))}
            className="rounded-[6px] bg-accent px-4 py-2 text-[13px] font-semibold text-base"
          >
            Sign out
          </button>
        </div>
      </div>
    );

  if (state !== "ok")
    return <div className="grid h-dvh place-items-center text-[13px] text-text-mute">Checking session…</div>;
  return <>{children}</>;
}
