"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { fetchMe } from "@/lib/api";
import { getAAL, signOut } from "@/lib/auth";
import { supabase } from "@/lib/supabase";

// Client-side route guard for the terminal. Requires, in order:
//  1) a Supabase session, 2) a 2FA-cleared (aal2) assurance level, and
//  3) backend authorization — /api/me succeeds only if the email is allowlisted.
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
      const aal = await getAAL();
      if (aal.current !== "aal2") {
        router.replace("/enroll-2fa");
        return;
      }
      const me = await fetchMe(); // null if the backend rejects (not allowlisted)
      if (active) setState(me ? "ok" : "unauthorized");
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
