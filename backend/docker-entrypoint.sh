#!/bin/sh
# Apply DB migrations, then run the app (single worker — see Dockerfile note).
set -e
echo "[entrypoint] running migrations…"
uv run alembic upgrade head
echo "[entrypoint] starting uvicorn (1 worker)…"
exec uv run uvicorn app.main:app --host 0.0.0.0 --port 8010
