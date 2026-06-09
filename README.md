# Delta Exchange (India) Options Paper-Trading Platform

A hosted, multi-user **paper-trading terminal** for Delta Exchange India options. Live option chain, strategy builder with payoff, **exact Delta margin**, realistic slippage fills, independent multi-strategy positions with per-leg & net PnL, real-time MTM, notes, and analytics — all simulated. **No order is ever placed on Delta** (read-only key only).

See `CLAUDE.md` for rules/stack, `software-development-master-checklist.md` for the dev process, and the approved plan at `~/.claude/plans/i-want-to-create-prancy-sloth.md`.

## Stack
- **Backend:** Python 3.12 / FastAPI · SQLAlchemy 2.0 async + Alembic · Postgres + TimescaleDB · Redis · numpy/scipy. Managed with **uv**.
- **Frontend:** Next.js (App Router) + TypeScript · Tailwind + shadcn/ui · lightweight-charts.

## Dev quickstart
```bash
# 1. Infra (Postgres+TimescaleDB + Redis)
docker compose up -d

# 2. Backend
cd backend
cp .env.example .env          # fill in DELTA_API_KEY/SECRET (read-only)
uv sync
uv run uvicorn app.main:app --reload --port 8010
#   health: http://localhost:8010/health   ws: ws://localhost:8010/ws
#   (port 8010 avoids colliding with other local services on 8000)

# 3. Frontend
cd ../frontend
cp .env.local.example .env.local
npm install
npm run dev                   # http://localhost:3000
```

## Tests
```bash
cd backend && uv run pytest -q && uv run ruff check .
```

## Status
Foundation + walking skeleton in place. Next: Phase-0 margin spike (needs Delta read-only key + a one-time logged-in capture), pure engines (payoff/PnL/slippage), then the Figma design → UI build.
