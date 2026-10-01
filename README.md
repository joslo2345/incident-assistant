# Incident Assistant for GPU Fleets

Replays GPU cluster telemetry, detects hardware anomalies, and has an agent investigate each
incident and return a cited diagnosis and suggested fix.

> Work in progress: A0 (foundation). The shared contracts and the ingestion API contract exist.

## Contracts

`libs/contracts` (`incident_contracts`) holds the Pydantic models every service shares:

- **Telemetry events** (`POST /v1/telemetry` body = `TelemetryBatch`, up to 1000 events):
  `gpu_metrics`, `xid`, `bmc_log`, discriminated by `event_type`.
- **Incident records**: created by the detector with evidence, then diagnosed by the agent.

## Ingestion API

`services/ingest` is a FastAPI app. The spec is in `docs/schemas/ingest.openapi.json`.

| Method | Path | Auth | Responses |
| --- | --- | --- | --- |
| POST | `/v1/telemetry` | `X-API-Key` header | 202 `{accepted, duplicates}`, 401, 422, 429 + `Retry-After` |
| GET | `/healthz` | none | 200 `{status: "ok"}` |

Valid keys come from `INGEST_API_KEYS` (comma-separated). With none set, every request is rejected.

```sh
INGEST_API_KEYS=dev-key uv run uvicorn ingest.app:app --reload   # docs at http://localhost:8000/docs
```

The JSON Schemas and OpenAPI spec in `docs/schemas/` are generated from code. A test fails if
they're out of date.

## Docs

- [Problem statement](docs/PROBLEM.md): who it's for and how success is measured
- [Architecture](docs/ARCHITECTURE.md): components, data flow, contracts
- [Decisions](docs/DECISIONS.md) and [Results](docs/RESULTS.md)

## Development

Requires [uv](https://docs.astral.sh/uv/) and Docker.

```sh
make install   # uv sync (Python 3.12)
make hooks     # install pre-commit hooks
make check     # lint, strict mypy, tests, schema drift check: the same as CI
make up        # build and start the local stack (ingest on :8000, key "dev-key")
make down
make schemas   # regenerate docs/schemas after changing a model or route
```
