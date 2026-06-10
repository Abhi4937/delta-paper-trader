# Paper-trader backend (FastAPI).
# IMPORTANT: single process only — the 1-second tick loop + in-memory series ring are
# per-process, so DO NOT run multiple uvicorn workers (you'd get duplicate ticks/writes
# and split state). One worker is correct for this app's scale.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY . .
RUN chmod +x docker-entrypoint.sh

EXPOSE 8010
CMD ["./docker-entrypoint.sh"]
