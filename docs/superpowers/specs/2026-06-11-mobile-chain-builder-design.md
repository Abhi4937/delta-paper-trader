# Mobile option-chain + builder + depth — design

_Date: 2026-06-11 · Scope: `frontend/` responsive behavior below the `lg` breakpoint (<1024px). Desktop (`lg+`) is unchanged._

## Problem

On phones the option chain is unusable (verified at 390×844 via Playwright + the live app):

- **Chain is cramped** — chain and the depth/builder panel split the screen height (`flex-col`), so the chain shows only ~5 rows.
- **Puts are cut off** — the table is `min-w-[640px]`; on a 390px screen it overflows and only the Calls side + Strike are visible, puts run off the right edge with no affordance.
- **ATM not centered** — `atmRef.scrollIntoView({block:"center"})` centers the ATM row vertically only; horizontally the table is pinned left.
- **Market depth dominates** — the `OrderBook` panel takes ~half the height below the chain at all times.
- **Cannot create a strategy on mobile (functional bug).** The B/S "add leg" buttons in `chain/page.tsx` (`MarkCell`) are `invisible … group-hover:visible`. Touch devices have no hover, so the buttons never appear → no leg can ever be added → no strategy can be built.

Reference: Delta Exchange's own mobile app (user screenshots in `SS/optin chain on small devices*.png`). We follow the user's explicit layout choices below; where they differ from Delta (column handling) the user's choice wins.

## Design

All changes are gated to `<lg`. Desktop keeps its current side-by-side layout (chain + (builder XOR orderbook)).

### 1. Chain gets the full screen; keep all columns; scroll both axes; center the ATM
- On mobile the chain table is the full-height region. Depth and builder no longer occupy inline height (depth → sheet, builder → page), so the chain is large.
- **Keep all 11 columns** (θ·IV·Δ·Mark·OI | STRIKE | OI·Mark·Δ·IV·θ) — no columns dropped, no Price/OI/Greeks tabs. The table stays `min-w-[640px]` inside a container that scrolls **both** horizontally and vertically (`overflow-auto`).
- **Auto-center the ATM on load** (and on underlying/expiry change): center the **ATM strike cell** both vertically (its row) and horizontally (the STRIKE column) within the scroll container — `atmStrikeCellRef.scrollIntoView({ block: "center", inline: "center" })`. Calls peek to the left, puts to the right; the user scrolls freely from there.
- The existing `centeredFor` guard (only re-center when underlying/expiry changes, not on every tick) is preserved so live updates don't yank the scroll position.

### 2. Add a leg by tapping the Mark (fixes the can't-build bug)
- In builder mode, the B/S buttons become **tappable on touch**, not hover-only. Tapping a strike's **Mark cell** reveals/raises the inline **`B | S`** buttons in that row (matches `SS/…click on leg…`). Tapping `B`/`S` calls the existing `selectLeg(contract, side)`.
- Desktop keeps the hover reveal (`group-hover:visible`). Implementation: show the buttons when `(hover) OR (coarse pointer)`, or on an explicit per-row "active" toggle set by tapping the Mark. Use a coarse-pointer check (`matchMedia('(pointer: coarse)')`) and/or a tapped-row state so desktop behavior is untouched.

### 3. Sticky "Done" action bar in builder mode (mobile)
- While in builder mode with ≥1 selected leg, a sticky bottom bar shows **`N Contracts Added · Clear All · Done`** (Done = accent/primary), matching `SS/…clear and done at bottom`.
- `Clear All` clears the basket (existing store action). `Done` does a **client-side `router.push("/chain/builder")`** (never a hard nav — that resets the Zustand basket, a known gotcha).

### 4. Full-screen builder page (`/chain/builder`, mobile)
- New route renders the existing `StrategyBuilder` (and its place/margin/payoff logic) full-screen, matching `SS/…strategy builder page`: header + close (back to chain), contract list (each leg: symbol, qty, price, edit), `+ Add contracts` (back to chain in builder mode), **Order Margin / Available Margin**, **`Analyse Payoff`** + **`Place Order`**.
- Reuses the current builder/payoff components and store actions — no engine/logic changes, only presentation + routing.
- Basket state lives in the Zustand store, shared across the chain and the builder page; client nav preserves it.

### 5. Market depth = bottom sheet, on demand (mobile)
- In non-builder mode, tapping a strike's Mark opens **Market Depth as a dismissable bottom sheet** over the chain (the chain stays mounted underneath). Only shown when a strike is tapped — it does not occupy height otherwise.
- The sheet wraps the existing `OrderBook` component (symbol/mark/ltp props unchanged).
- Desktop keeps its persistent side `OrderBook` panel.

### 6. Desktop unchanged
- `lg+` keeps `flex-row`, the inline `StrategyBuilder`/`OrderBook` side panel, and hover-reveal B/S. No regression.

## Accessibility / touch (folded in from the UI/UX review — apply while touching these components)

These are P0 issues that overlap the same components, so we fix them as part of this work (mobile-scoped where they'd affect desktop density):
- **44px touch targets** — the B/S add buttons, qty +/-, leg-remove, close-leg, and bottom-nav items are <24px. Give interactive controls a ≥44×44 hit area on touch (expand hit area with padding or a `before:absolute before:-inset-*` pseudo-element; add `touch-action: manipulation`). The new tap-revealed B/S buttons must meet this.
- **Contrast** — `--color-text-mute (#848a94)` on surface is ≈4.0:1 (fails WCAG AA) yet carries real data. Promote data-bearing mute text to `text-text-dim`, or lighten the token to ≥`#9aa0ab`.
- **Font floor** — set a floor of 11px for secondary, 12–13px for primary data on mobile; number inputs `text-[16px]` on mobile to stop iOS zoom-on-focus.
- **Focus + keyboard** — add a global `:focus-visible` ring; make the chain's tradeable cells real buttons (or `role="button"` + `tabIndex` + Enter) so the build flow is keyboard-reachable; add `aria-label` to icon-only controls.
- **Device-aware copy** — the builder empty-state "Hover a strike and click B/S" must become tap-oriented on touch.

(Out of scope here but logged for later: `prefers-reduced-motion`, radius/spacing token cleanup, Analytics `CumChart` aspect ratio, Place-Order-disabled-until-margin-loads — tracked in the QA notes, not this spec.)

## Components affected

- `frontend/src/app/(terminal)/chain/page.tsx` — responsive layout (full-height chain on mobile), both-axis scroll, ATM both-axis centering, touch-aware B/S reveal, sticky Done bar (mobile), open depth sheet on tap (mobile).
- `frontend/src/components/MarketDepthSheet.tsx` (**new**) — mobile bottom sheet wrapping `OrderBook`.
- `frontend/src/app/(terminal)/chain/builder/page.tsx` (**new**) — full-screen builder page (mobile) reusing `StrategyBuilder`.
- `frontend/src/components/StrategyBuilder.tsx` — minor: render correctly both as the desktop side panel and inside the full-screen mobile page (presentation only).

## Testing

- **Unit (vitest):** any pure helper extracted (e.g. ATM-centering target selection) is tested. Touch-reveal logic kept as a small testable predicate.
- **Manual / Playwright (390×844, dev `NEXT_PUBLIC_E2E` bypass):**
  1. Chain fills the screen; ATM strike is centered horizontally + vertically on load; both-axis scroll works; puts reachable.
  2. Builder mode: tap Mark → `B|S` appear → tap adds a leg (the bug is fixed); Done bar shows correct count.
  3. `Done` → builder page renders the basket, margin, Analyse Payoff, Place Order; basket preserved across nav.
  4. Non-builder: tap Mark → depth sheet opens with the right symbol; dismiss returns to chain.
  5. Desktop (≥1024px) layout unchanged (regression check).

## Non-goals / out of scope

- No change to chain data, greeks, margin, fees, or any engine logic — presentation + routing + the touch fix only.
- No column-group tabs (`Price/OI/Greeks`) — the user chose all-columns + scroll instead.
- Desktop layout is not redesigned.
- The chart per-second render cost (separate work) is not part of this.
