FROM python:3.12.8-slim-bookworm AS builder

ENV UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:0.5.30 /uv /uvx /usr/local/bin/
COPY pyproject.toml uv.lock .python-version /app/
COPY apps/api/pyproject.toml /app/apps/api/pyproject.toml
COPY workers/codex-worker/pyproject.toml /app/workers/codex-worker/pyproject.toml
RUN uv sync --frozen --package monitor-api --no-dev

COPY apps/api/api /app/apps/api/api
COPY apps/api/app /app/apps/api/app
COPY apps/api/alembic.ini /app/apps/api/alembic.ini
COPY apps/api/migrations /app/apps/api/migrations
COPY config /app/config

FROM python:3.12.8-slim-bookworm AS runtime

ENV PATH=/app/.venv/bin:$PATH \
    PYTHONPATH=/app/apps/api \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
RUN groupadd --system monitor && useradd --system --gid monitor --home-dir /app monitor
COPY --from=builder --chown=monitor:monitor /app/.venv /app/.venv
COPY --from=builder --chown=monitor:monitor /app/apps/api /app/apps/api
COPY --from=builder --chown=monitor:monitor /app/config /app/config
USER monitor

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/v1/health', timeout=5).read()"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
