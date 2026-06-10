# Pre-commit checklist

Run these before committing. CI (`.github/workflows/ci.yml`) runs the **gating** ones on
every push/PR; the **non-blocking** ones surface tracked debt.

## Backend (`cd backend`)
```bash
uv run pytest -q          # GATING — 82 tests (engines, exit, slippage, vault, auth)
uv run ruff check         # non-blocking (line-length / import-order debt)
uv run mypy app           # non-blocking (dict/Task type-arg debt)
```

## Frontend (`cd frontend`)
```bash
npx tsc --noEmit          # GATING — type safety
npm run test              # GATING — vitest (chart data-prep, exit logic)
npx next build            # GATING — production build compiles
npx eslint .              # non-blocking (set-state-in-effect debt)
```

## What the gates catch
- **`chartData.test.ts`** asserts the position chart's net/leg/IV lines get one point per
  sample — the exact contract the lightweight-ring regression broke.
- **`test_exit_engine.py`** asserts auto-exit is suspended on stale data and the SL/TP/
  close-scope logic — the risk-critical paths.
- **`tsc` + `next build`** catch type breaks and build breaks before they ship.

## Known debt (tracked, not yet gating)
Pre-existing repo-wide lint/type issues kept the lint gates non-blocking for now:
~21 ruff (E501 line length, E741 `l`, I001 import order), 9 mypy (`dict`/`Task` type args),
7 eslint (set-state-in-effect, refs-during-render). Clean these up to flip the gates on.

## Manual smoke (until the Playwright E2E is wired into CI)
After backend/frontend changes that touch the live flow, sanity-check in the browser:
build a strategy → place → open Positions → the **MTM/IV/Delta panels render with leg lines**
→ close. (This is the flow the automated E2E will cover — see Task 18.)
