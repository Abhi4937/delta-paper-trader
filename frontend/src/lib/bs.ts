// Black-Scholes pricing + greeks (r = 0 for crypto). Mirrors the backend engine
// so the prototype's payoff / greeks / margin behave like the real thing.

export type OptType = "call" | "put";

function ncdf(x: number): number {
  // Abramowitz-Stegun approximation of the standard normal CDF.
  const t = 1 / (1 + 0.2316419 * Math.abs(x));
  const d = 0.3989423 * Math.exp((-x * x) / 2);
  const p =
    d *
    t *
    (0.3193815 +
      t * (-0.3565638 + t * (1.781478 + t * (-1.821256 + t * 1.330274))));
  return x > 0 ? 1 - p : p;
}

function npdf(x: number): number {
  return 0.3989423 * Math.exp((-x * x) / 2);
}

export function bsPrice(
  type: OptType,
  S: number,
  K: number,
  T: number,
  sigma: number,
): number {
  if (T <= 0 || sigma <= 0 || S <= 0)
    return type === "call" ? Math.max(S - K, 0) : Math.max(K - S, 0);
  const d1 = (Math.log(S / K) + 0.5 * sigma * sigma * T) / (sigma * Math.sqrt(T));
  const d2 = d1 - sigma * Math.sqrt(T);
  return type === "call"
    ? S * ncdf(d1) - K * ncdf(d2)
    : K * ncdf(-d2) - S * ncdf(-d1);
}

export interface Greeks {
  delta: number;
  gamma: number;
  theta: number;
  vega: number;
}

export function greeks(type: OptType, S: number, K: number, T: number, sigma: number): Greeks {
  if (T <= 0 || sigma <= 0) return { delta: 0, gamma: 0, theta: 0, vega: 0 };
  const sq = sigma * Math.sqrt(T);
  const d1 = (Math.log(S / K) + 0.5 * sigma * sigma * T) / sq;
  const d2 = d1 - sq;
  const delta = type === "call" ? ncdf(d1) : ncdf(d1) - 1;
  const gamma = npdf(d1) / (S * sq);
  const vega = (S * npdf(d1) * Math.sqrt(T)) / 100; // per 1 vol-pt
  const theta = (-(S * npdf(d1) * sigma) / (2 * Math.sqrt(T))) / 365; // per day
  return { delta, gamma, theta, vega };
}
