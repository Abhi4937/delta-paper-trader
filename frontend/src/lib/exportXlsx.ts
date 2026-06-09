// Export a paper strategy to a multi-sheet .xlsx: Summary + Legs + the time-series
// behind every analytics panel (MTM / IV / Δ / Θ-Vega) + Notes. All money in USD
// (the engine's base unit), so the numbers are chartable straight in Excel.

import * as XLSX from "xlsx";
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
