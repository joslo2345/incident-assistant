# Incident Assistant for GPU Fleets

Replays GPU cluster telemetry, detects hardware anomalies, and has an agent investigate each
incident and return a cited diagnosis and suggested fix.

> Work in progress: A4 (knowledge base and RAG) is done; next is A5 (MCP tools and the investigation agent).
>
> **Detector, final version: 99% recall, 100% precision, 0/12 decoy alarms** over 72 injected faults ([report](eval/reports/detection.md)).

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

`make up` starts everything with Docker Compose (`deploy/compose.yaml`). The first run generates
random local secrets in `deploy/.env` (gitignored). All ports are bound to localhost only; see [SECURITY.md](SECURITY.md).

| Service | URL | What it is |
| --- | --- | --- |
| Ingest API | http://localhost:8000/docs | `POST /v1/telemetry`, key `dev-key` |
| Grafana | http://localhost:3000 | Fleet health and System health dashboards; log in as `admin`, password `GRAFANA_ADMIN_PASSWORD` in `deploy/.env` |
| Prometheus | http://localhost:9090 | metrics from ingest, consumer, Redpanda |
| TimescaleDB | localhost:5432 | db `telemetry`; roles `postgres` (migrations), `telemetry_writer`, `grafana_reader`; passwords in `deploy/.env` |
| Knowledge API | http://localhost:8001/docs | `/v1/search`, `/v1/ask`, `/v1/chunks/{id}`; key `KNOWLEDGE_API_KEY` in `deploy/.env` |
| Agent API | http://localhost:8002/docs | investigate, runs and traces, approvals; keys `AGENT_API_KEY` and `APPROVER_API_KEY` in `deploy/.env` |
| Redpanda | localhost:19092 | Kafka API; topics `telemetry.raw` (8 partitions), `telemetry.dlq` |

Data flow: ingest → `telemetry.raw` → consumer → TimescaleDB (`gpu_metrics`, `xid_events`, `bmc_log`,
rollups `gpu_metrics_1m` / `gpu_metrics_1h`). `make test-integration` kills the consumer mid-replay,
forces redelivery and sends a bad message, then checks that nothing was lost or duplicated.

## Anomaly detection

`services/detector` turns telemetry into incidents in three layers: static thresholds and XID/BMC
event rules, per-GPU z-scores, and a thermal model that predicts each GPU's temperature from its
power. The model separates "too hot for its load" (a cooling fault) from "busy" (a noisy neighbor).
It runs live in the stack and writes `incidents` (the shared `Incident` contract) for the agent (A5).

```sh
uv run replay --duration 1d --speed 0 --start now-2d --run-id ev1 --faults 14 --bmc-dropout 0.5
uv run python scripts/eval_detection.py --run-id ev4 --duration 2d --seed 37   # full evaluation
```

## Knowledge base

`services/knowledge` holds 22 runbooks and 64 past-incident reports (`knowledge/`), searchable through
hybrid search (pgvector + full-text, fused with RRF) and a local cross-encoder reranker
(**recall@5 100%** on the test set, [report](eval/reports/retrieval.md)). `POST /v1/ask` answers with
citations to the exact runbook section, and says "not enough information" when the sources don't
cover the question.

```sh
make up                       # also ingests the corpus
uv run knowledge eval         # retrieval evaluation (vector / keyword / hybrid / rerank)
curl -s localhost:8001/v1/ask -H "X-API-Key: $KNOWLEDGE_API_KEY" -H 'content-type: application/json' \
  -d '{"query": "XID 79 on a GPU, what now?"}'
```

Answers use Claude when `ANTHROPIC_API_KEY` is set in `deploy/.env`; otherwise they're extractive
(the best cited passage).

## Investigation agent

`services/agent` investigates an incident the way an on-call engineer would: it reads the GPU
metrics and the XID/BMC logs around the incident, searches the runbooks, and submits a structured
diagnosis (root cause, confidence, evidence, citations, recommended action). Citations must be
chunks a tool returned during the run. A drain or GPU reset is never performed: it becomes a
pending approval request that a person decides with a separate key. Every model and tool call is
traced in Postgres with tokens and latency.

The model is open source and runs locally: Qwen3 30B-A3B through [Ollama](https://ollama.com)
(about 18 GB; 32 GB of RAM or more recommended). Any OpenAI-compatible server works, and
`AGENT_PROVIDER=anthropic` switches to Claude.

```sh
brew install ollama && brew services start ollama
make model                    # pulls the model and builds qwen3-agent (32k context)
make up                       # the agent investigates new live incidents automatically
uv run python scripts/agent_env.py uv run agent investigate <incident-id>
uv run python scripts/agent_env.py uv run agent approvals --status pending
curl -s -X POST localhost:8002/v1/approvals/<request-id>/decision -H "X-API-Key: $APPROVER_API_KEY" \
  -H 'content-type: application/json' -d '{"decision": "approve", "decided_by": "alice"}'
```

The same tools are an MCP server (`make mcp`, stdio). To try them in the MCP Inspector:
`npx @modelcontextprotocol/inspector uv run python scripts/agent_env.py uv run agent mcp`
(from the repo root). Seven tools; all read-only except `drain_node`, which only files a request.

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
- [Security](SECURITY.md): threat model, controls, review log

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
