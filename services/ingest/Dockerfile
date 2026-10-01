# Build context is the repo root: docker build -f services/ingest/Dockerfile .
FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0
WORKDIR /app

# Dependencies first, so code changes don't invalidate this layer.
COPY pyproject.toml uv.lock README.md ./
COPY libs/contracts/pyproject.toml libs/contracts/
COPY services/ingest/pyproject.toml services/ingest/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package incident-ingest --no-install-workspace

COPY libs/contracts libs/contracts
COPY services/ingest services/ingest
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package incident-ingest --no-editable

FROM python:3.12-slim
RUN useradd --system --uid 10001 app
COPY --from=build /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1
USER app
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')"]
CMD ["uvicorn", "ingest.app:app", "--host", "0.0.0.0", "--port", "8000"]
