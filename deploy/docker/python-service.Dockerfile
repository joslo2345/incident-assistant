# One image recipe for every Python service in the uv workspace.
# Build from the repo root:
#   docker build -f deploy/docker/python-service.Dockerfile \
#     --build-arg SERVICE_DIR=services/ingest --build-arg PACKAGE=incident-ingest .
FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0
ARG SERVICE_DIR
ARG PACKAGE
WORKDIR /app

# Dependencies first, so code changes don't invalidate this layer.
COPY pyproject.toml uv.lock README.md ./
COPY libs/contracts/pyproject.toml libs/contracts/
COPY ${SERVICE_DIR}/pyproject.toml ${SERVICE_DIR}/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package ${PACKAGE} --no-install-workspace

COPY libs/contracts libs/contracts
COPY ${SERVICE_DIR} ${SERVICE_DIR}
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package ${PACKAGE} --no-editable

FROM python:3.12-slim
RUN useradd --system --uid 10001 app
COPY --from=build /app/.venv /app/.venv
# Migrations ship in every image; only the consumer's migrate command uses them.
COPY deploy/db/migrations /app/migrations
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1 MIGRATIONS_DIR=/app/migrations
WORKDIR /app
USER app
