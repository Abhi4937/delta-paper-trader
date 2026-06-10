"use client";

import { LogOut, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import type { Me } from "@/lib/api";
import { hasTotp, signOut } from "@/lib/auth";

export default function UserMenu({ me }: { me: Me | null }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [mfa, setMfa] = useState<boolean | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    hasTotp().then(setMfa);
  }, []);
  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  if (!me) return null;

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        title={me.email}
        className="grid h-7 w-7 place-items-center rounded-full bg-surface-3 text-[12px] font-semibold text-text hover:bg-surface"
      >
        {me.email.charAt(0).toUpperCase()}
      </button>

      {open && (
        <div className="absolute right-0 top-9 z-50 w-64 overflow-hidden rounded-lg border border-line bg-surface-2 p-1 shadow-xl">
          <div className="px-3 py-2">
            <div className="truncate text-[12px] text-text">{me.email}</div>
            <div className="text-[10px] text-text-mute">
              {me.isAdmin ? "Admin" : "User"} ·{" "}
              {mfa === null ? "…" : mfa ? "2FA enabled" : "2FA off"}
            </div>
          </div>
          <div className="my-1 h-px bg-line" />
          <Link
            href="/enroll-2fa"
            onClick={() => setOpen(false)}
            className="flex items-center gap-2 rounded-[6px] px-3 py-1.5 text-[12px] text-text-dim hover:bg-surface hover:text-text"
          >
            <ShieldCheck size={14} className={mfa ? "text-pos" : "text-text-mute"} />
            {mfa ? "Two-factor auth · enabled" : "Set up two-factor auth"}
          </Link>
          <button
            onClick={() => signOut().then(() => router.replace("/login"))}
            className="flex w-full items-center gap-2 rounded-[6px] px-3 py-1.5 text-[12px] text-text-dim hover:bg-surface hover:text-neg"
          >
            <LogOut size={14} /> Sign out
          </button>
        </div>
      )}
    </div>
  );
}
