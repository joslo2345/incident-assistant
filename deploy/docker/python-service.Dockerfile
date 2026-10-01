# One image recipe for every Python service in the uv workspace.
# Build from the repo root:
#   docker build -f deploy/docker/python-service.Dockerfile \
#     --build-arg SERVICE_DIR=services/ingest --build-arg PACKAGE=incident-ingest .
# Base images are pinned by digest (Dependabot keeps them current).
FROM python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f AS build
COPY --from=ghcr.io/astral-sh/uv:0.11@sha256:77280f2f771df71f90786c314fe1bbc1e023feac652969bbf139c280babf2eb7 /uv /bin/uv
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

FROM python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f
# Pick up Debian security fixes released after the base image was built.
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 app
COPY --from=build /app/.venv /app/.venv
# Migrations ship in every image; only the consumer's migrate command uses them.
COPY deploy/db/migrations /app/migrations
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1 MIGRATIONS_DIR=/app/migrations
WORKDIR /app
USER app
