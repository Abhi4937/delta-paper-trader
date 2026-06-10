"use client";

import { useCallback, useEffect, useState } from "react";
import {
  type AdminUser,
  type AllowlistEntry,
  type UserLog,
  addAllowlist,
  fetchAdminUsers,
  fetchAllowlist,
  fetchUserLogs,
  removeAllowlist,
} from "@/lib/api";

const fmt = (t: number | null) =>
  t ? new Date(t).toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) : "—";

export default function AdminPage() {
  const [users, setUsers] = useState<AdminUser[] | null>(null);
  const [allow, setAllow] = useState<AllowlistEntry[]>([]);
  const [authorized, setAuthorized] = useState<boolean | null>(null);
  const [email, setEmail] = useState("");
  const [logsFor, setLogsFor] = useState<{ user: AdminUser; logs: UserLog[] } | null>(null);

  const load = useCallback(async () => {
    const [u, a] = await Promise.all([fetchAdminUsers(), fetchAllowlist()]);
    if (u === null) {
      setAuthorized(false);
      return;
    }
    setAuthorized(true);
    setUsers(u);
    setAllow(a ?? []);
  }, []);

  useEffect(() => {
    // async admin data load; state is set only after the awaits resolve.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load();
  }, [load]);

  async function add() {
    const e = email.trim().toLowerCase();
    if (!e) return;
    await addAllowlist(e);
    setEmail("");
    load();
  }
  async function remove(e: string) {
    await removeAllowlist(e);
    load();
  }
  async function showLogs(user: AdminUser) {
    const logs = await fetchUserLogs(user.id);
    setLogsFor({ user, logs: logs ?? [] });
  }

  if (authorized === false)
    return <div className="grid h-full place-items-center text-[13px] text-text-mute">Admin access only.</div>;

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-3 border-b border-line px-4 py-2.5">
        <h1 className="text-[13px] font-semibold tracking-tight text-text">Admin</h1>
        <span className="text-[11px] text-text-mute">{users?.length ?? 0} users · {allow.length} allowlisted</span>
      </div>

      <div className="min-h-0 flex-1 space-y-4 overflow-auto p-4">
        {/* allowlist */}
        <div className="rounded-lg border border-line bg-surface-2 p-3">
          <div className="mb-2 text-[11px] font-medium text-text-dim">Allowlist — who may sign in</div>
          <div className="mb-3 flex gap-2">
            <input value={email} onChange={(e) => setEmail(e.target.value)} placeholder="email@example.com"
              onKeyDown={(e) => e.key === "Enter" && add()}
              className="flex-1 max-w-sm rounded-[6px] border border-line bg-surface px-3 py-1.5 text-[12px] text-text outline-none focus:border-accent" />
            <button onClick={add} className="rounded-[6px] bg-accent px-3 py-1.5 text-[12px] font-semibold text-base">Add</button>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {allow.map((a) => (
              <span key={a.email} className="flex items-center gap-1.5 rounded-[5px] border border-line bg-surface px-2 py-1 text-[11px] text-text-dim">
                {a.email}
                <button onClick={() => remove(a.email)} className="text-text-mute hover:text-neg" title="Remove">×</button>
              </span>
            ))}
          </div>
        </div>

        {/* users */}
        <div className="rounded-lg border border-line bg-surface-2">
          <div className="grid grid-cols-[2fr_1fr_1fr_1fr_auto] gap-2 border-b border-line px-3 py-1.5 text-[9px] uppercase tracking-wider text-text-mute">
            <span>Email</span><span>Role</span><span>Login</span><span>Joined</span><span></span>
          </div>
          {(users ?? []).map((u) => (
            <div key={u.id} className="grid grid-cols-[2fr_1fr_1fr_1fr_auto] items-center gap-2 border-t border-line/40 px-3 py-1.5 text-[12px]">
              <span className="truncate text-text-dim">{u.email}{!u.isActive && <span className="ml-1 text-[9px] text-neg">disabled</span>}</span>
              <span className={u.isAdmin ? "text-accent" : "text-text-mute"}>{u.isAdmin ? "admin" : "user"}</span>
              <span className="text-text-mute">{u.hasLogin ? "linked" : "—"}</span>
              <span className="tnum text-[11px] text-text-mute">{fmt(u.createdAt)}</span>
              <button onClick={() => showLogs(u)} className="text-[11px] text-accent hover:underline">Logs</button>
            </div>
          ))}
          {users && users.length === 0 && <div className="px-3 py-6 text-center text-[12px] text-text-mute">No users yet.</div>}
        </div>

        {/* logs panel */}
        {logsFor && (
          <div className="rounded-lg border border-line bg-surface-2">
            <div className="flex items-center justify-between border-b border-line px-3 py-1.5">
              <span className="text-[11px] font-medium text-text-dim">Logs · {logsFor.user.email}</span>
              <button onClick={() => setLogsFor(null)} className="text-[11px] text-text-mute hover:text-text-dim">close</button>
            </div>
            {logsFor.logs.length === 0 ? (
              <div className="px-3 py-4 text-center text-[12px] text-text-mute">No logs.</div>
            ) : (
              logsFor.logs.map((l, i) => (
                <div key={i} className="grid grid-cols-[auto_auto_1fr] gap-2 border-t border-line/40 px-3 py-1 text-[11px]">
                  <span className="tnum text-text-mute">{fmt(l.t)}</span>
                  <span className="font-medium text-text-dim">{l.action}</span>
                  <span className="truncate text-text-mute">{l.detail}</span>
                </div>
              ))
            )}
          </div>
        )}
      </div>
    </div>
  );
}
