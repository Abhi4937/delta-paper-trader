// Export a paper strategy to a multi-sheet .xlsx: Summary + Legs + the time-series
// behind every analytics panel (MTM / IV / Δ / Θ-Vega) + Notes. All money in USD
// (the engine's base unit), so the numbers are chartable straight in Excel.

import * as XLSX from "xlsx";
import type { LiveJournal } from "./api";
import { USDINR } from "./store";
import type { Leg, Position } from "./types";

const legLabel = (l: Leg) => `${l.side === "buy" ? "+" : "-"}${l.strike}${l.type === "call" ? "CE" : "PE"}`;
const fmtT = (t: number) =>
  new Date(t).toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", second: "2-digit" });

const realized = (p: Position) => p.legs.reduce((s, l) => (l.status === "closed" ? s + (l.exitGross ?? 0) - (l.exitFees ?? 0) : s), 0);
const fees = (p: Position) => p.legs.reduce((s, l) => s + (l.status === "closed" ? l.exitFees ?? 0 : 0), 0);

export function exportPositionXlsx(p: Position): void {
  const wb = XLSX.utils.book_new();
  const legs = p.legs;
  const sheet = (name: string, rows: (string | number)[][]) =>
    XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(rows), name);

  // Summary
  sheet("Summary", [
    ["Strategy", p.name],
    ["Underlying", p.underlying],
    ["Expiry", p.expiry],
    ["Status", p.status],
    ["Opened", fmtT(p.openedAt)],
    ["Closed", p.closedAt ? fmtT(p.closedAt) : ""],
    ["Close reason", p.closeReason ?? ""],
    ["Legs", legs.length],
    ["Margin (USD)", p.margin],
    ["Margin source", p.marginBadge],
    ["Combined target (USD)", p.targetPnl ?? ""],
    ["Combined SL (USD)", p.stopLossAmount ?? ""],
    ["Combined SL (% margin)", p.stopLossPctOfMargin ?? ""],
    ["Realized net (USD)", realized(p)],
    ["Fees paid (USD)", fees(p)],
    ["", ""],
    ["Note", "All monetary values are in USD; greeks are net position values."],
  ]);

  // Legs
  sheet("Legs", [
    ["Leg", "Symbol", "Side", "Qty", "Type", "Strike", "Entry", "Mark@entry", "Spot@entry", "Status", "Exit price", "Exit reason", "Exit at", "Gross (USD)", "Fees (USD)", "Net (USD)"],
    ...legs.map((l) => [
      legLabel(l), l.symbol, l.side, l.qty, l.type, l.strike, l.entry, l.markAtEntry, l.spotAtEntry, l.status,
      l.exitPrice ?? "", l.exitReason ?? "", l.exitAt ? fmtT(l.exitAt) : "",
      l.exitGross ?? "", l.exitFees ?? "", l.exitGross != null && l.exitFees != null ? l.exitGross - l.exitFees : "",
    ]),
  ]);

  // Panel series
  const expiries = [...new Set(legs.map((l) => l.expiry))];
  sheet("MTM", [
    ["Time", "Net PnL (USD)", ...legs.map(legLabel)],
    ...p.series.map((s) => [fmtT(s.t), s.pnl, ...legs.map((l) => s.legs[l.id]?.pnl ?? "")]),
  ]);
  sheet("IV", [
    ["Time", ...expiries.map((e) => `ATM IV ${e}`), ...legs.map((l) => `${legLabel(l)} IV`)],
    ...p.series.map((s) => [fmtT(s.t), ...expiries.map((e) => s.atmIv[e] ?? ""), ...legs.map((l) => s.legs[l.id]?.iv ?? "")]),
  ]);
  sheet("Delta", [
    ["Time", "Net Delta (BTC)", ...legs.map((l) => `${legLabel(l)} Δ`)],
    ...p.series.map((s) => [fmtT(s.t), s.delta, ...legs.map((l) => s.legs[l.id]?.delta ?? "")]),
  ]);
  sheet("Theta-Vega", [
    ["Time", "Net Theta (USD/day)", "Net Vega (USD/vol-pt)"],
    ...p.series.map((s) => [fmtT(s.t), s.theta, s.vega]),
  ]);

  if (p.notes.length) {
    sheet("Notes", [["Kind", "Time", "Note"], ...p.notes.map((n) => [n.kind, fmtT(n.at), n.body])]);
  }

  const safe = p.name.replace(/[^\w]+/g, "_");
  XLSX.writeFile(wb, `${safe}_${p.id.slice(0, 6)}.xlsx`);
}

// Live trade journal → .xlsx: Trades (one row per trade: times, P&L, max/min MTM, max DD),
// Legs (every leg's entry/exit), Live log. Money in USD with a ₹ column (fixed 85).
export function exportLiveJournalXlsx(j: LiveJournal): void {
  const wb = XLSX.utils.book_new();
  const sheet = (name: string, rows: (string | number)[][]) =>
    XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(rows), name);
  const t = (ms: number | null) => (ms ? fmtT(ms) : "");
  const n = (v: number | null | undefined) => (v == null ? "" : +v.toFixed(4));
  const inr = (v: number | null | undefined) => (v == null ? "" : Math.round(v * USDINR));
  const dur = (s: number) => `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;

  sheet("Trades", [
    ["Trade", "Status", "Opened", "Closed", "Duration", "P&L $", "P&L ₹", "Fees $", "Max MTM $", "Max MTM ₹",
      "Max MTM at", "Min MTM $", "Min MTM ₹", "Min MTM at", "Max drawdown $", "Max drawdown ₹", "DD peak at",
      "DD trough at", "Close reason", "Armed", "Basket SL $", "Legs"],
    ...j.trades.map((r) => [
      r.name, r.status, t(r.openedAt), t(r.closedAt), dur(r.durationSeconds), n(r.pnl), inr(r.pnl), n(r.fees),
      n(r.maxMtm), inr(r.maxMtm), t(r.maxMtmAt), n(r.minMtm), inr(r.minMtm), t(r.minMtmAt),
      n(r.maxDrawdown), inr(r.maxDrawdown), t(r.drawdownPeakAt), t(r.drawdownTroughAt),
      r.closeReason ?? "", r.armed ? "yes" : "no", n(r.basketStopLoss), r.legs.length,
    ]),
  ]);
  sheet("Legs", [
    ["Trade", "Symbol", "Side", "Qty", "Entry", "Exit", "Exit at", "Exit reason", "P&L $", "P&L ₹", "Fees $",
      "SL trigger (premium)", "Target trigger (premium)", "Delta bracket SL", "Status"],
    ...j.trades.flatMap((r) => r.legs.map((l) => [
      r.name, l.symbol, l.side, l.qty, l.entry, n(l.exit), t(l.exitAt), l.exitReason ?? "", n(l.pnl), inr(l.pnl),
      n(l.fees), n(l.slPrice), n(l.tpPrice), n(l.deltaStopPrice), l.status,
    ])),
  ]);
  sheet("Live log", [["Time", "Action", "Detail"], ...j.logs.map((l) => [t(l.t), l.action, l.detail])]);
  XLSX.writeFile(wb, `live-journal-${new Date().toISOString().slice(0, 10)}.xlsx`);
}
