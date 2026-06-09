# Margin Model Research — full knowledge base

**Goal:** make our local portfolio-margin model match Delta Exchange India's Strategy-Builder margin **exactly** (to the rupee/cent), for any multi-leg options basket, so the paper trader shows true margin even without a live token.

**Status:** Live endpoint = exact (source of truth). Local model = **10/12 strategy types at 95–103%**; 3 exotic classes off (iron condor / double calendar over-estimate ~1.3–1.6×; risk reversal under ~0.80). This doc captures everything for a future dedicated session to close the gap.

**Companion files:** `docs/adr/0001-margin-source.md` (auth/source decision), `docs/adr/0002-portfolio-margin-model.md` (the model + calibration log). Engine: `backend/app/engines/margin.py`. Service: `backend/app/services/margin.py`. Calibration harnesses: `backend/scripts/capture_margin_calibration.py`, `backend/scripts/calibrate_strategies.py`. Captured data: `backend/tests/fixtures/margin_calibration_btc.json`.

---

## 1. The live estimator (source of truth)

- **Endpoint:** `POST https://cdn.india.deltaex.org/v2/orders/estimate_margin/basket` (also on `api.india.delta.exchange`). It is an **estimator — it places NOTHING**.
- **Auth:** header **`authorization: <token>` — RAW web-session token, NO "Bearer" prefix.** Proven: it does NOT accept HMAC API keys (signed key → flat `{"error":"Unauthorized"}` from a different middleware than the structured `Signature Mismatch` the api-key validator returns). The token is the Delta web app's session token (opaque, ~51 chars, not a JWT → can't decode expiry). Get it from `localStorage` (Console: `Object.entries(localStorage).filter(([k,v])=>/auth|token|jwt/i.test(k+v))`) or any logged-in request's `authorization` header. Stored in `.env` as `DELTA_WEB_JWT`.
- **Request:**
  ```json
  { "index_symbol": ".DEXBTUSD",        // BTC; ETH = .DEXETHUSD
    "orders": [ {"product_id": 137818, "side": "sell", "size": 1,
                 "order_type": "market_order", "time_in_force": "gtc"} ],
    "source": "desktop" }
  ```
  **Order id field MUST be `product_id` or `product_symbol` — `symbol` is rejected** (`only one of either product_id or product_symbol must be sent`).
- **Response:** `result.portfolio_margin`, `additional_required_margin`, `total_cash_inflow`, `total_cash_outflow`, `new_orders[]` (hypothetical). Margin is in **USD** (settlement currency; wallet assets are USD). INR display needs a USD→INR rate (TODO at UI layer).
- **Durability:** token expires → 401 `unauthorized`. Refresh: re-grab manually now; later capture the **refresh token** for auto-refresh. One service-account token serves all hosted users (a fresh basket's margin is account-standalone when the account holds no positions).

---

## 2. Delta's documented methodology (Portfolio Margin)

Source: https://guides.delta.exchange/delta-exchange-india-user-guide/trading-guide/margin-explainer/portfolio-margin

```
Margin             = max(Risk Margin, Margin Floor)
Initial Margin     = max(Risk Margin, Margin Floor) − UCF
Maintenance Margin = 0.80 × (Initial Margin + UCF) − UCF          (≈ 80% of IM)
```

**Risk Margin** = worst portfolio loss across **29 stress scenarios**:
- Price shocks: `S·(1 + level·price_span)`, level ∈ {0, ±0.33, ±0.50, ±0.67, ±1.00} (9).
- Vol shocks: σ unchanged / +IV_up / −IV_down (3) → 27.
- 2 extreme: `S·(1 ± 3·price_span)` with vol-up, loss weighted ×1/3. → 29.
- Risk Margin is **mark-to-market portfolio VALUE loss** ("revaluation losses"), not cashflow.

**Shock spans (BTC, scale with portfolio notional N):**
```
price_span   = clip(0.01 + 4e-8·(N−100_000), 0.01, 0.10)
vol_dn_span  = clip(0.06 + 1.2e-7·(N−100_000), 0.06, 0.30)
vol_up_span  = clip(0.09 + 1.8e-7·(N−100_000), 0.09, 0.45)
```
**IV shocks (DTE-adjusted):** `IV = span · (30/DTE)^0.30` (1 DTE, 45% span → ±124.8%, matches the doc example).

**Margin Floor:**
```
OM%        = clip(0.005 + 5e-9·(N−200_000), 0.005, 0.02)   # BTC; ETH cap 0.05, other 0.10
short_opt  = Σ max(0.05·premium, OM%·notional)             # short legs
long_opt   = Σ min(premium, max(0.05·premium, OM%·notional))# long legs
futures    = FM%·max(long_notional, short_notional)
```
per leg `premium = |qty|·cv·mark`, `notional = |qty|·cv·spot`.

**UCF (Unrealised Cashflows):** doc says *"for futures, UCF = unrealised PnL; for options, UCF = the expected pay-off."*

**Liquidation:** triggered when equity ≤ Maintenance Margin (≈80% of IM). On breach: cancel orders → add delta-hedge perp (if ≥$100K) → scale positions. Fees: futures `0.25%·notional·1.18`; options `min(0.5·premium, 0.25%·notional)·1.18`.

**Mark price (options)** = orderbook impact bid/ask averaged to "impact size", **capped to BS(model IV ± 25%)**. (Relevant to slippage, ADR 0003.)

---

## 3. Our local engine (`app/engines/margin.py`)

`IM = max( max(Risk, Floor), Σ long-premiums ) − UCF`  (UCF currently 0); `MM = 0.8·(IM+UCF) − UCF`.

- **Black-Scholes** priced locally via `math.erf` (r=0 for crypto). `bs_price`, `implied_vol` (bisection).
- **`implied_vol` anchoring:** each option's IV is inverted from its **mark** so `V_base == mark` exactly — removes the BS-vs-mark basis (critical; see §4).
- 29-scenario `scenarios()`, `_risk_margin()`, `_margin_floor()`, params in `MarginParams` (Delta's BTC constants, calibratable).
- `defined_max_loss()` — payoff-engine max loss over a wide grid (kept as a **UI risk utility**; not used in the margin formula after the cap was reverted).
- **Long-premium term:** a long option's margin == its premium paid (confirmed, §4).

---

## 4. Issues found & how we solved them

1. **Auth (API key rejected).** *Solved:* raw `authorization: <token>` web session, not HMAC. (§1)
2. **Order id field.** *Solved:* `product_id`/`product_symbol`, not `symbol`. (§1)
3. **BS-vs-mark basis.** Our Black-Scholes underpriced vs Delta's mark (~2.3×), wrecking long-option margin. *Solved:* use **mark-implied IV** (`implied_vol`) so V_base == mark.
4. **Side-label bug (the big one).** The calibration harness passed Delta's `"buy"/"sell"` straight into `MarginLeg` (which expects `"long"/"short"`), so every long leg was scored as short. Longs looked 0.27–0.44×. *Solved:* map `buy→long, sell→short`. Took 8/9 baskets to 0.95–1.03. **The engine was correct all along.**
5. **Long-option margin.** Confirmed via cash fields: **long margin == premium paid** (`long call margin 2.178 == cash_outflow 2.163 == qty·cv·mark`); shorts are risk-based. *Solved:* `max(…, Σ long-premiums)` term.
6. **Iron condor over-charge.** Root cause was NOT risk (correctly tiny 0.167) but the per-leg **Floor (1.265)** with no offset. Tried a **defined-max-loss cap for net-credit structures** → fixed one width (1.00) but broke across widths and contradicted the data (see §5). *Reverted.* Now over-estimates conservatively; real fix = UCF (§6).

---

## 5. Calibration data & key relationships (the evidence)

**9-basket capture (10 DTE), `local/delta` after the side fix:** short call 0.97, long call 0.99, short put 0.96, short straddle 0.95, long straddle 0.99, short strangle 0.95, bull call spread 1.03, **iron condor 1.60→ (see below)**, short call ×10 0.97.

**12-strategy broad run:** verticals (bull/bear × call/put) 0.96–1.03, straddles 0.95–0.99, **calendar (call/put) 0.98, diagonal 1.02** (multi-expiry works!), **risk reversal 0.80**, **double calendar 1.13**, **iron condor 1.23**.

**Condor across wing widths (shorts fixed ±3%) — the crucial finding:**
| wing | delta margin | our max-loss | net credit | **margin + credit** |
|---|---|---|---|---|
| ±5% | 1.093 | 0.799 | 0.670 | **1.763** |
| ±6% | 0.863 | 1.076 | 0.900 | **1.763** |
| ±7% | 0.694 | 1.295 | 1.177 | 1.871 |

- **`margin + net_credit ≈ const (1.763)`** for fixed short legs ⇒ **`margin = gross − net_credit`, i.e. UCF = net credit** (matches the doc's "UCF = expected payoff"). Wider wings → more credit → *less* margin — the **opposite** of a max-loss cap.
- Delta's margin can **exceed** the theoretical max loss (1.093 > 0.799) → it is NOT a clean defined-risk cap.
- The "gross" (1.763) ≠ our per-leg Floor (1.27). The ±7% point breaks the constant (noise / strike snapping).

**Naked-short UCF ≈ 0** (short call: delta 0.86 == our max(Risk,Floor); premium received does NOT reduce margin because risk is unbounded). So **UCF reduces margin only for DEFINED-RISK / spread structures**, not naked — a conditional interaction, not a clean global term.

**Risk reversal decomposition:** combo 1.366 vs short-put 0.757 + long-call 1.111 (sum 1.868) — partial offset, directional (net long delta).

**Lot-size / notional scaling (short ATM call, varying size) — VALIDATED:**
| size | notional $ | delta | local | ratio | margin/contract |
|---|---|---|---|---|---|
| 1 | 64 | 0.875 | 0.877 | 1.00 | 0.875 |
| 500 | 31,854 | 437.5 | 438.4 | 1.00 | 0.875 |
| 2,000 | 127,415 | 1,808 | 1,891 | 1.05 | 0.904 |
| 10,000 | 637,077 | 22,044 | 22,610 | 1.03 | 2.204 |
| 40,000 | 2,548,308 | 270,156 | 268,949 | 1.00 | 6.754 |
- Margin is **linear in size below ~$100K notional** (spans floored) and **super-linear above** (spans widen with N) — at 40k lots, margin/contract is **7.7×** the small-size value. **Our model matches Delta 1.00–1.05 across $64 → $2.5M notional**, so the notional-scaling spans are correct. Minor 1.05 divergence sits right at the $100K knee (calibration nuance).

---

## 6. Open problems (for the dedicated session)

1. **UCF = net credit, but only for defined-risk structures.** Need to: (a) detect "defined-risk" robustly, (b) compute the **gross** `max(Risk, Floor)` the way Delta does (our Floor undercounts the gross ~1.27 vs ~1.76), (c) subtract `UCF = net credit`. Net effect should turn condor/double-calendar from ~1.3–1.6× to ~1.0×.
2. **Gross Floor mismatch.** Our per-leg Floor (`Σ`) ≠ Delta's gross. Likely Delta nets/offsets within the floor (cf. futures floor uses `max(long,short)` notional, not sum). Investigate whether options floor nets by strike/side too.
3. **Risk reversal / directional under-charge.** The ±1–3% price-shock band undercounts directional (delta) risk for net-directional positions. Confirm Delta's effective price range and whether risk is evaluated at strikes (SPAN-style) vs only ±span%.
4. **Extreme-scenario multiplier (3.0×span) & the 1/3 weighting** — unverified; affects tails.
5. **Units / INR.** Estimator returns USD; need USD→INR for display. Confirm `contract_value` per underlying (BTC 0.001) and any lot multipliers.
6. **`mark_vol` units** — old skill claimed IV×100; live India `/v2/tickers` returns a fraction (0.49 = 49%). Verify per field before any math.
7. **Maintenance margin & liquidation buffer** — implement `MM = 0.8(IM+UCF)−UCF` and the liquidation-distance display; validate MM against the live account (needs positions, or the `additional_required_margin` field).
8. **ETH / other underlyings** — different caps (OM% 0.05/0.10). Re-capture and re-fit per underlying.

---

## 7. How to improve — the plan

1. **Continuous calibration loop (already designed):** every live `get_margin` logs `(inputs, delta_margin, local_margin, divergence)` to `margin_quotes`. Accumulate across times/conditions/strikes/underlyings → a real dataset.
2. **Batch capture grids:** extend `scripts/calibrate_strategies.py` to sweep DTE × moneyness × width × underlying, recording **all** response fields (`portfolio_margin`, `additional_required_margin`, cash in/out). Solve for the unknowns (gross Floor, UCF, effective range) by **fitting**, not hand-derivation — there are ≥3 interacting unknowns; one point can't separate them.
3. **Per-class models:** verticals/straddles/calendars already match — freeze them as regression fixtures. Focus fitting on credit spreads (UCF) and directional (range).
4. **Validation harness:** a pytest that replays the captured fixtures and asserts `local/delta` within tolerance per strategy class (tighten over time).
5. **Independence target:** once the local model matches within a tight tolerance over a large capture set, promote it toward primary so the app no longer depends on a live token for exactness.

---

## 8. References

- Portfolio Margin: https://guides.delta.exchange/delta-exchange-india-user-guide/trading-guide/margin-explainer/portfolio-margin
- Margin Explainer: https://guides.delta.exchange/delta-exchange-india-user-guide/trading-guide/margin-explainer
- Fair Price Marking: https://guides.delta.exchange/delta-exchange-india-user-guide/trading-guide/fair-price-marking
- Allowed Trading Bands: https://guides.delta.exchange/delta-exchange-india-user-guide/exchange-sop-and-policies/allowed-trading-bands
- Order Types: https://guides.delta.exchange/delta-exchange-india-user-guide/trading-guide/order-types
- API docs: https://docs.delta.exchange/ · Python client: https://github.com/delta-exchange/python-rest-client
- ADRs: `docs/adr/0001-margin-source.md`, `docs/adr/0002-portfolio-margin-model.md`, `docs/adr/0003-slippage-model.md`
