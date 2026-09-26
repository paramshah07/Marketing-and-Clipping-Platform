# One recipe, two targets: api (no ffmpeg) and worker (adds ffmpeg). compose.yml sets the commands.
FROM ghcr.io/astral-sh/uv:python3.12-trixie-slim AS base
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/venv \
    PATH="/venv/bin:$PATH" PYTHONPATH=/app PYTHONUNBUFFERED=1 \
    PROCRASTINATE_APP=app.tasks.queue.app
WORKDIR /app
# venv lives in /venv so the dev bind mount of ./backend over /app doesn't hide it
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project
ENTRYPOINT []

FROM base AS api
COPY . .

FROM base AS worker
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
COPY . .
