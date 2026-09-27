# Railway (PLAN-backend.md §14): un solo servicio Docker + plugin de Postgres.
# El CMD corre las migraciones de pyway antes de levantar uvicorn — Railway
# no ofrece un hook de "release phase" separado, así que va en el arranque.
FROM python:3.13-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

COPY pyproject.toml uv.lock* ./
RUN uv sync --no-dev --no-install-project

COPY . .
RUN uv sync --no-dev

EXPOSE 8000
CMD ["sh", "-c", "uv run python scripts/migrate_deploy.py && uv run uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
