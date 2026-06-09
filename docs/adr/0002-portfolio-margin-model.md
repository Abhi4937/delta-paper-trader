# ADR 0002 — Portfolio-margin model (replicate Delta's methodology)

**Status:** Accepted (2026-06-09)
**Context:** v1 needs exact Delta margin. The live `estimate_margin/basket` endpoint (ADR 0001) is the source of truth but needs a session token. We need an always-on local engine that **replicates Delta's published portfolio-margin methodology** (not the inaccurate `max(premium, 1.5×expiry_loss)` from the old project), calibrated continuously against the endpoint.

Source: Delta India guide — *Portfolio Margin*.

## Formulas
```
Margin             = max(Risk Margin, Margin Floor)
Initial Margin     = max(Risk Margin, Margin Floor) − UCF        (UCF = unrealised cashflows)
Maintenance Margin = 0.80 × (Initial Margin + UCF) − UCF         (≈ 80% of IM)
```

### Risk Margin = worst loss over 29 stress scenarios
- **Price shocks:** spot × (1 + level), level ∈ {0, ±0.33, ±0.50, ±0.67, ±1.00} × **price_span** (9 levels).
- **Vol shocks:** σ unchanged / +IV_up / −IV_down (3 states) → 27 scenarios.
- **2 extreme:** spot × (1 ± 3.0×price_span) with vol-up; loss counted × **1/3**. → 29 total.
- Revalue every leg with Black-Scholes at shocked (spot, σ), same DTE; `pnl = Σ signed_qty·cv·(V_shock − V_base)`; `RiskMargin = max(0, max scenario loss)`.

### Shock spans (scale with portfolio notional N, BTC)
```
price_span   = clip(0.01 + 4e-8·(N−100_000), 0.01, 0.10)
vol_dn_span  = clip(0.06 + 1.2e-7·(N−100_000), 0.06, 0.30)
vol_up_span  = clip(0.09 + 1.8e-7·(N−100_000), 0.09, 0.45)
```
### IV shocks (DTE-adjusted)
```
IV_up   = vol_up_span · (30/DTE)^0.30
IV_down = vol_dn_span · (30/DTE)^0.30      # 1 DTE,45% span → ±124.8% (matches doc)
σ_shock = max(σ ± IV, ε)
```
### Margin Floor
```
OM%        = clip(0.005 + 5e-9·(N−200_000), 0.005, 0.02)   # BTC; ETH cap 0.05
short_opt  = Σ max(0.05·premium, OM%·notional)             # over short legs
long_opt   = Σ min(premium, max(0.05·premium, OM%·notional))# over long legs
futures    = FM%·futures_notional                          # FM% same scaling
Margin Floor = short_opt + long_opt + futures
```
where per leg `premium = |qty|·cv·mark`, `notional = |qty|·cv·spot`.

## Decision
- Implement structurally exactly as above (`app/engines/margin.py`), parameters in a `MarginParams` dataclass so **calibration tunes constants** against the endpoint. Black-Scholes priced locally (numpy/scipy), r≈0 for crypto.
- Baseline value `V_base` = BS at current (spot, mark IV) for consistency; the BS-vs-mark basis is a calibration residual.
- `MarginService`: `WebEstimatorProvider` (exact, token) → `LocalPortfolioMargin` (this). Log divergence every time both run; tune until within tolerance.
- Surface **IM, MM, and liquidation buffer** (`equity − MM`) per strategy. Liquidation fees: futures `0.25%·notional·1.18`; options `min(0.5·premium, 0.25%·notional)·1.18`.

## Consequences
- Exact-match depends on calibration vs the endpoint; until calibrated, the local number is "est". Constants above are Delta's published values for BTC; ETH/other differ by caps.
- Extreme-scenario price multiplier (3.0×span) and UCF handling are the least-certain bits — flagged for calibration.

## Calibration log (`tests/fixtures/margin_calibration_btc.json`)
**The live endpoint works server-side** (auth: raw `authorization: <token>` — no "Bearer"; order field `product_id`/`product_symbol`; returns exact `portfolio_margin`). Confirmed via cash fields: **a long option's margin == its premium** (long call margin 2.178 == cash outflow 2.163 == `qty·cv·mark`); shorts are risk-based (premium received separately as cash inflow). So `margin = max( max(Risk, Floor), Σ long-premiums ) − UCF`.

9 BTC baskets (10 DTE), `local/delta`:
- **All 9 within 0.92–1.21**; 8/9 at 0.92–1.00 (short call/put/straddle/strangle, long call, long straddle, bull call spread, short ×10).
- **Iron condor cracked:** the inflated number was NOT risk (correctly 0.167) but the per-leg **Floor (1.265)** + long-premium with no offset recognition. Delta's condor margin ≈ defined max loss (wing width − credit). Fix: **`margin = min( max(max(Risk,Floor), long_premium), defined_max_loss )` applied only to NET-CREDIT structures** (`Σ short premium ≥ Σ long premium`). This caps credit spreads at their max loss while leaving debit spreads/longs funded by premium and naked shorts unaffected. Condor improved **1.60× → ~1.0–1.2×**.
- **Residual:** Delta's exact condor = `Floor − UCF` (UCF = expected payoff), slightly below raw max loss, so the cap can mildly over-charge some credit spreads. Pinning UCF needs more captures (varying widths) — continuous-calibration work; live endpoint is exact meanwhile.

**Engine logic:** `IM = min( max(max(Risk, Floor), Σ long-premiums), defined_max_loss[if net-credit] ) − UCF`; `MM = 0.8·(IM+UCF) − UCF`.

## Broad strategy validation (`scripts/calibrate_strategies.py`)
12 strategies vs live estimator — **10/12 match 0.95–1.03**: bull/bear call & put verticals, long/short straddle, call/put **calendar**, call **diagonal** (multi-expiry handled). Covers every credit/debit × long/short-vega quadrant.

**Final decision (the max-loss cap was reverted):** multi-width condor captures showed `margin + net_credit ≈ const` ⇒ **for net-credit defined-risk spreads, `margin = gross − net_credit`, i.e. UCF = net credit** (matches the doc's "UCF = expected payoff"). But Delta's *gross* Floor (~1.76) ≠ our per-leg Floor (~1.27) and the fit is noisy across strikes, and Delta's margin can even exceed the theoretical max loss — so the max-loss cap was unprincipled/unreliable and is removed.

**Residual gaps (continuous-calibration; live endpoint is exact meanwhile):**
- **Iron condor / double calendar OVER-estimate (~1.3–1.6×):** we don't subtract `UCF = net credit`. This is the **conservative/safe** direction for a fallback (reserves more, never less).
- **Risk reversal UNDER-estimates (~0.80):** directional (delta) risk undercounted by the narrow ±1–3% price-shock band.
Target to close both: fit `gross Floor` + `UCF = net credit` from accumulated captures in the backend calibration loop.
