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

## Development

```sh
uv sync                                  # install (Python 3.12)
uv run pytest                            # tests
uv run ruff check . && uv run mypy libs services scripts tests
uv run python scripts/export_schemas.py  # regenerate docs/schemas after changing a model or route
```
