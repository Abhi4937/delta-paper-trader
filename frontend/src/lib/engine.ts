// Client-side engines: payoff, PnL, and a believable margin estimate (mirrors the
// backend's shape: risk stress + floor + long-premium). Good enough for the
// prototype; the real app uses the Python engines + live Delta estimator.

import { bsPrice } from "./bs";
import type { Leg } from "./types";

export interface Payoff {
  prices: number[];
  pnl: number[];
  breakevens: number[];
  maxProfit: number;
  maxLoss: number;
}

function signed(leg: Leg): number {
  return leg.side === "buy" ? leg.qty : -leg.qty;
}

function legExpiryValue(leg: Leg, price: number): number {
  const intrinsic =
    leg.type === "call" ? Math.max(price - leg.strike, 0) : Math.max(leg.strike - price, 0);
  return intrinsic;
}

export function payoffCurve(legs: Leg[], spot: number, points = 161): Payoff {
  const lo = spot * 0.8;
  const hi = spot * 1.2;
  const prices: number[] = [];
  const pnl: number[] = [];
  for (let i = 0; i < points; i++) {
    const p = lo + ((hi - lo) * i) / (points - 1);
    let total = 0;
    for (const leg of legs)
      total += signed(leg) * (legExpiryValue(leg, p) - leg.entry) * leg.contractValue;
    prices.push(p);
    pnl.push(total);
  }
  const breakevens: number[] = [];
  for (let i = 1; i < pnl.length; i++) {
    if (Math.sign(pnl[i - 1]) !== Math.sign(pnl[i]) && pnl[i] !== pnl[i - 1]) {
      const t = pnl[i - 1] / (pnl[i - 1] - pnl[i]);
      breakevens.push(prices[i - 1] + t * (prices[i] - prices[i - 1]));
    }
  }
  return {
    prices,
    pnl,
    breakevens,
    maxProfit: Math.max(...pnl),
    maxLoss: Math.min(...pnl),
  };
}

// USD P/L for a leg (premium is in Delta price-points; × contractValue -> USD).
export function legPnl(leg: Leg, mark: number): number {
  return signed(leg) * (mark - leg.entry) * leg.contractValue;
}

export function netPnl(legs: Leg[], marks: Record<string, number>): number {
  return legs.reduce((s, l) => s + legPnl(l, marks[l.symbol] ?? l.entry), 0);
}

// Margin estimate: worst loss over a price-shock grid (BS-repriced) + floor,
// with long options funded by premium.
// USD margin estimate (fallback when the live Delta estimator is unavailable).
export function estimateMargin(legs: Leg[], spot: number): number {
  if (legs.length === 0) return 0;
  const T = legs[0].dte / 365;
  let risk = 0;
  for (let s = 0.92; s <= 1.081; s += 0.02) {
    const S = spot * s;
    let pnl = 0;
    for (const leg of legs) {
      const v = bsPrice(leg.type, S, leg.strike, T, 0.5);
      pnl += signed(leg) * (v - leg.entry) * leg.contractValue;
    }
    risk = Math.max(risk, -pnl);
  }
  let floor = 0;
  let longPrem = 0;
  for (const leg of legs) {
    const cv = leg.contractValue;
    const prem = leg.qty * leg.entry * cv;
    const notional = leg.qty * spot * cv;
    const base = Math.max(0.05 * prem, 0.005 * notional);
    if (leg.side === "sell") floor += base;
    else {
      floor += Math.min(prem, base);
      longPrem += prem;
    }
  }
  return Math.max(Math.max(risk, floor), longPrem);
}
