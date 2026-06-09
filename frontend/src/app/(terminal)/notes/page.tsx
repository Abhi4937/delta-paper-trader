"use client";

import { useState } from "react";
import clsx from "clsx";
import { money, positionPnl, useStore } from "@/lib/store";
import type { Position } from "@/lib/types";

const fmtT = (t: number) =>
  new Date(t).toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });

export default function NotesPage() {
  const positions = useStore((s) => s.positions);
  useStore((s) => s.tickN); // live PnL
  const open = positions.filter((p) => p.status === "open");
  const closed = positions.filter((p) => p.status === "closed");
  const ordered = [...open, ...closed];

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-3 border-b border-line px-4 py-2.5">
        <h1 className="text-[13px] font-semibold tracking-tight text-text">Notes</h1>
        <span className="text-[11px] text-text-mute">trade journal · {positions.length} strategies</span>
      </div>
      <div className="min-h-0 flex-1 overflow-auto p-4">
        {ordered.length === 0 ? (
          <div className="grid h-full place-items-center text-[13px] text-text-mute">No strategies yet — place one to start journaling.</div>
        ) : (
          <div className="mx-auto max-w-3xl space-y-3">
            {ordered.map((p) => <NoteCard key={p.id} p={p} />)}
          </div>
        )}
      </div>
    </div>
  );
}

function NoteCard({ p }: { p: Position }) {
  const currency = useStore((s) => s.currency);
  const addNote = useStore((s) => s.addNote);
  const [kind, setKind] = useState<"entry" | "exit">("entry");
  const [body, setBody] = useState("");
  const closed = p.status === "closed";
  const pnl = positionPnl(p);

  const submit = () => {
    const text = body.trim();
    if (!text) return;
    addNote(p.id, kind, text);
    setBody("");
  };

  return (
    <div className="rounded-lg border border-line bg-surface-2">
      <div className="flex items-center justify-between border-b border-line/60 px-3 py-2">
        <div className="flex items-center gap-2">
          <span className="text-[12px] font-semibold text-text">{p.name}</span>
          <span className="text-[10px] text-text-mute">{p.underlying} · {p.expiry}</span>
          {closed && <span className="rounded-[3px] bg-surface-3 px-1.5 py-0.5 text-[9px] uppercase tracking-wider text-text-mute">closed</span>}
        </div>
        <span className={clsx("tnum text-[12px] font-medium", (closed ? realized(p) : pnl) >= 0 ? "text-pos" : "text-neg")}>
          {closed ? "realized " : "UPNL "}{money(closed ? realized(p) : pnl, currency)}
        </span>
      </div>

      <div className="space-y-1.5 px-3 py-2">
        {p.notes.length === 0 ? (
          <p className="text-[11px] text-text-mute">No notes yet.</p>
        ) : (
          p.notes
            .slice()
            .sort((a, b) => b.at - a.at)
            .map((n, i) => (
              <div key={i} className="flex gap-2 text-[12px]">
                <span className={clsx("mt-0.5 h-fit rounded-[3px] px-1 py-0.5 text-[8px] uppercase tracking-wider", n.kind === "entry" ? "bg-info/15 text-info" : "bg-warn/15 text-warn")}>{n.kind}</span>
                <div className="min-w-0 flex-1">
                  <p className="whitespace-pre-wrap break-words text-text-dim">{n.body}</p>
                  <span className="text-[10px] text-text-mute">{fmtT(n.at)}</span>
                </div>
              </div>
            ))
        )}
      </div>

      <div className="flex items-end gap-2 border-t border-line/60 px-3 py-2">
        <div className="flex overflow-hidden rounded-[4px] border border-line text-[10px]">
          {(["entry", "exit"] as const).map((k) => (
            <button key={k} onClick={() => setKind(k)} className={clsx("px-2 py-1 capitalize", kind === k ? "bg-surface-3 text-text" : "text-text-mute hover:text-text-dim")}>{k}</button>
          ))}
        </div>
        <textarea
          value={body}
          onChange={(e) => setBody(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submit(); }}
          rows={1}
          placeholder="Add a note (thesis, adjustment, lesson)… ⌘/Ctrl+Enter"
          className="min-h-[32px] flex-1 resize-y rounded-[4px] border border-line bg-surface px-2 py-1.5 text-[12px] text-text outline-none placeholder:text-text-mute focus:border-accent"
        />
        <button onClick={submit} disabled={!body.trim()} className="rounded-[4px] bg-accent px-3 py-1.5 text-[11px] font-medium text-black disabled:opacity-40">Add</button>
      </div>
    </div>
  );
}

// realized = sum over closed legs of (gross − fees)
function realized(p: Position): number {
  return p.legs.reduce((s, l) => (l.status === "closed" ? s + (l.exitGross ?? 0) - (l.exitFees ?? 0) : s), 0);
}
