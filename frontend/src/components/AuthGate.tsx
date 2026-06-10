"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { getAAL } from "@/lib/auth";
import { supabase } from "@/lib/supabase";

// Client-side route guard for the terminal: requires a session AND a 2FA-cleared
// (aal2) assurance level — matching the backend, which rejects anything below aal2.
export default function AuthGate({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [ready, setReady] = useState(false);

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
      if (active) setReady(true);
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

  if (!ready)
    return <div className="grid h-dvh place-items-center text-[13px] text-text-mute">Checking session…</div>;
  return <>{children}</>;
}
