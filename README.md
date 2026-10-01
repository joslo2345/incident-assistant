# Incident Assistant for GPU Fleets

Replays GPU cluster telemetry, detects hardware anomalies, and has an agent investigate each
incident and return a cited diagnosis and suggested fix.

> Work in progress: A2 (streaming, storage, observability) is done; next is A3 (anomaly detection).

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

## Local stack

`make up` starts everything with Docker Compose (`deploy/compose.yaml`):

| Service | URL | What it is |
| --- | --- | --- |
| Ingest API | http://localhost:8000/docs | `POST /v1/telemetry`, key `dev-key` |
| Grafana | http://localhost:3000 | Fleet health and System health dashboards (anonymous view; admin/admin) |
| Prometheus | http://localhost:9090 | metrics from ingest, consumer, Redpanda |
| TimescaleDB | localhost:5432 | db `telemetry`, user/password `postgres` |
| Redpanda | localhost:19092 | Kafka API; topics `telemetry.raw` (8 partitions), `telemetry.dlq` |

Data flow: ingest → `telemetry.raw` → consumer → TimescaleDB (`gpu_metrics`, `xid_events`, `bmc_log`,
rollups `gpu_metrics_1m` / `gpu_metrics_1h`). `make test-integration` kills the consumer mid-replay,
forces redelivery and sends a bad message, then checks that nothing was lost or duplicated.

## Telemetry replayer

`replayer/` turns a day of real GPU-cluster load ([Alibaba cluster-trace-gpu-v2020](https://github.com/alibaba/clusterdata/tree/master/cluster-trace-gpu-v2020),
CC BY 4.0) into DCGM-style metrics and Redfish BMC entries for a simulated fleet of 8 nodes × 8 GPUs
(5 H100, 3 A100; see `replayer/fleet.toml`), and sends it to the ingest API.

```sh
make up
uv run replay --duration 10m --speed 10          # 10 simulated minutes in 1 minute
uv run replay --duration 3d --speed 0 --start now-3d   # backfill 3 days as fast as possible
```

The workload slice is committed in `replayer/data/`. To rebuild it from the raw trace (~1 GB download):
`scripts/download_alibaba_trace.sh && uv run replay-prepare`.

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
