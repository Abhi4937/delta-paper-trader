"use client";

import { useEffect, useState } from "react";
import { type KeyStatus, fetchKeys, saveKeys } from "@/lib/api";

const SLOTS: { kind: string; label: string; hint: string }[] = [
  { kind: "delta_trade_key", label: "Delta trade API key", hint: "Used ONLY for live auto-close (reduce-only). Stored encrypted; not used yet." },
  { kind: "delta_trade_secret", label: "Delta trade API secret", hint: "Paired secret for the trade key." },
  { kind: "delta_web_jwt", label: "Delta web session token", hint: "Optional — for the exact Strategy-Builder margin number (else a local estimate is used)." },
];

export default function ApiKeysPage() {
  const [status, setStatus] = useState<KeyStatus>({});
  const [inputs, setInputs] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    fetchKeys().then((s) => s && setStatus(s));
  }, []);

  async function save() {
    const body = Object.fromEntries(Object.entries(inputs).filter(([, v]) => v !== ""));
    if (Object.keys(body).length === 0) return;
    setBusy(true);
    setMsg(null);
    const s = await saveKeys(body);
    if (s) {
      setStatus(s);
      setInputs({});
      setMsg("Saved.");
    } else {
      setMsg("Save failed.");
    }
    setBusy(false);
  }

  async function clearSlot(kind: string) {
    setBusy(true);
    const s = await saveKeys({ [kind]: "" });
    if (s) setStatus(s);
    setBusy(false);
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-3 border-b border-line px-4 py-2.5">
        <h1 className="text-[13px] font-semibold tracking-tight text-text">Settings · API Keys</h1>
        <span className="text-[11px] text-text-mute">Encrypted at rest — values are never shown back.</span>
      </div>

      <div className="min-h-0 flex-1 space-y-3 overflow-auto p-4">
        <div className="max-w-2xl space-y-3">
          <div className="rounded-lg border border-accent/30 bg-accent/10 px-3 py-2 text-[11px] text-text-dim">
            <span className="font-medium text-accent">Optional.</span> Paper trading needs no keys —
            the platform provides the live market data. Add your own Delta keys only for live-account
            features (monitoring/closing your real Delta positions). The web session token is optional
            too; without it, margin uses a local estimate.
          </div>
          {SLOTS.map((s) => {
            const set = status[s.kind];
            return (
              <div key={s.kind} className="rounded-lg border border-line bg-surface-2 p-3">
                <div className="mb-1 flex items-center justify-between">
                  <span className="text-[12px] font-medium text-text">{s.label}</span>
                  <span className={`rounded-[3px] px-1.5 py-0.5 text-[9px] uppercase tracking-wider ${set ? "bg-pos/15 text-pos" : "bg-surface-3 text-text-mute"}`}>
                    {set ? "set" : "not set"}
                  </span>
                </div>
                <p className="mb-2 text-[10px] text-text-mute">{s.hint}</p>
                <div className="flex items-center gap-2">
                  <input
                    type="password" autoComplete="off"
                    placeholder={set ? "•••••••• (enter to replace)" : "paste value"}
                    value={inputs[s.kind] ?? ""}
                    onChange={(e) => setInputs((p) => ({ ...p, [s.kind]: e.target.value }))}
                    className="flex-1 rounded-[6px] border border-line bg-surface px-3 py-1.5 text-[12px] text-text outline-none focus:border-accent"
                  />
                  {set && (
                    <button onClick={() => clearSlot(s.kind)} disabled={busy}
                      className="rounded-[6px] border border-line px-2 py-1.5 text-[11px] text-text-mute hover:text-neg disabled:opacity-50">
                      Clear
                    </button>
                  )}
                </div>
              </div>
            );
          })}

          <div className="flex items-center gap-3 pt-1">
            <button onClick={save} disabled={busy}
              className="rounded-[6px] bg-accent px-4 py-2 text-[13px] font-semibold text-base disabled:opacity-50">
              {busy ? "Saving…" : "Save changes"}
            </button>
            {msg && <span className="text-[12px] text-text-dim">{msg}</span>}
          </div>
        </div>
      </div>
    </div>
  );
}
