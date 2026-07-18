FROM node:22-alpine AS frontend-build
WORKDIR /build/frontend
RUN corepack enable
COPY frontend/package.json frontend/pnpm-lock.yaml* ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm build

FROM ghcr.io/astral-sh/uv:0.8.4 AS uv

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FRONTEND_DIST=/app/frontend/dist \
    PATH=/app/.venv/bin:$PATH
WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /uvx /bin/
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY babymonitorvl/ ./babymonitorvl/
RUN uv sync --frozen --no-dev
COPY --from=frontend-build /build/frontend/dist ./frontend/dist
EXPOSE 8000
CMD ["uvicorn", "babymonitorvl.main:app", "--host", "0.0.0.0", "--port", "8000"]
