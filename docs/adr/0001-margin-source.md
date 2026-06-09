# ADR 0001 — Exact margin source: web-session estimator + calibrated local model

**Status:** Accepted (2026-06-09)
**Context:** v1 requires margin that matches Delta's Strategy Builder exactly, and the project is paper-only (no orders ever placed).

## Findings (from the Phase-0 spike)
- Delta's Strategy Builder gets pre-placement basket margin from
  **`POST /v2/orders/estimate_margin/basket`** (host `cdn.india.deltaex.org`, also on `api.india.delta.exchange`).
- Request: `{ index_symbol, orders: [{size, side, order_type, time_in_force, symbol}], source }`.
  Response: `{ result: { portfolio_margin, additional_required_margin, total_cash_inflow, total_cash_outflow, new_orders[] } }`.
  It is an **estimator — it places nothing** (the `new_orders` are hypothetical).
- **Auth:** the endpoint does **NOT** accept Delta HMAC API keys. Proven by error-shape diff:
  - Bad HMAC on `/v2/wallet/balances` → structured `{"error":{"code":"Signature Mismatch",...}}` (reaches the api-key validator).
  - `estimate_margin/basket` with a signed key → flat `{"error":"Unauthorized"}` (different middleware).
  It requires the **web-session bearer JWT** (the token the browser sends after login); the response echoes `user_id`.

## Decision
1. **Exact source:** call `estimate_margin/basket` with a captured **web-session JWT** (`DELTA_WEB_JWT`). Used READ-ONLY for estimation.
2. **Durability:** JWTs expire, so this can't be the always-on source for a hosted app. Use it to **calibrate a local Black-76 portfolio-margin model**, which becomes the always-available source. UI badge: `● matched` (live token) vs `est` (local model) vs `stale`.
3. **Safety:** the web client is hard-restricted to **only** `POST /v2/orders/estimate_margin/basket` and read paths; it must refuse every order-placing endpoint. No order is ever placed. (The API key remains read-only for market data + account reads.)

## Consequences
- Need a token-refresh story (manual paste now; investigate login/refresh later). Between refreshes the calibrated local model serves.
- `MarginService` providers: `WebEstimatorProvider` (exact, token-gated) → `LocalStressProvider` (calibrated fallback), with `basket_hash` cache + shadow divergence logging.
