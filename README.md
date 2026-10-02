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
| Web UI | http://localhost:3001 | incidents, diagnoses, approvals, follow-up chat, audit, feedback; `make demo-users` creates `alice` (approver) and `victor` (viewer), passwords in `deploy/.env` |
| Agent API | http://localhost:8002/docs | investigate, runs and traces (key `AGENT_API_KEY`); people routes for the UI and Slack bot |
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

## Web UI and Slack bot

A technician signs in, sees incidents with the agent's diagnosis, checks the evidence against the
telemetry, opens the cited runbook passages, approves or rejects the requested action, and asks
follow-up questions. Every decision lands in an append-only audit log; thumbs up/down on any
diagnosis or answer feeds a report of candidate eval cases.

| | |
| --- | --- |
| ![Incident detail](docs/images/03-incident.jpg) | ![Pending approval](docs/images/04-pending-approval.jpg) |
| Diagnosis, cited runbook sections, GPU telemetry with the detector's signals | A fresh investigation files a drain request; only approvers see the buttons |
| ![Approved](docs/images/06-approved.jpg) | ![Audit log](docs/images/07-audit.jpg) |
| Decided under the signed-in user's name | Append-only audit log (written by a database trigger) |
| ![Follow-up](docs/images/08-followup.jpg) | ![Feedback](docs/images/09-feedback.jpg) |
| Follow-up questions run the same agent, read-only | Feedback report |

```sh
make up && make demo-users     # then open http://localhost:3001
make web-dev                   # UI with hot reload on :5173
node web/scripts/screenshots.mjs   # re-run the whole flow in a browser and refresh these images
```

**Slack** (`uv run agent slack`, Socket Mode, so nothing has to be reachable from the internet):
posts new incidents to a channel, threads the diagnosis with Approve/Reject and feedback buttons,
and answers questions asked in the thread. It acts as the Slack user who clicked, through the
same API as the web UI, so only linked approvers can decide
(`agent users add alice --role approver --slack U012ABC`). It needs a Slack app with a bot token
(`SLACK_BOT_TOKEN`), an app-level token (`SLACK_APP_TOKEN`) and `SLACK_CHANNEL`; it is tested
against a fake Slack client and the real API.

## Evaluation

`harness/` scores the whole system against injected faults with known answers. Each eval set has
one case per fault, labelled with the true failure type, the runbook that covers it, and the
actions that runbook allows (`harness/src/evaluation/sets.py`). Sets include noisy-neighbor
decoys and faults whose BMC logs were never collected. `dev` (25 incidents) is for improvement
rounds; `test` (25) is held out and only scored at the start and the end.

```sh
make eval-data                 # replay the labelled faults and build the sets (~15 min)
make eval                      # dev set, local model + judge; report in eval/reports/a6/dev/
make eval SET=test LABEL=final # the held-out set
make eval-calibrate REPORT=eval/reports/a6/dev/r0-baseline.json   # judge vs hand grades
uv run evaluate compare eval/reports/a6/dev/*.json                  # rounds side by side
```

Scored in code: root cause, action allowed by the runbook, unsafe actions (hardware action on a
decoy), missed actions (real fault left in service), right runbook cited, latency, tokens, cost.
An LLM judge (Gemma 3 12B, a different model family from the agent) scores only citation support,
and is checked against hand grades. Every report is tagged with the git commit and prompt hash.

CI can't run the local model, so `make eval-ci` replays recorded model replies through the real
stack on a 10-incident set: tools, validation and scoring run for real, and the build fails if a
run breaks, tool errors rise, or scores drop below the recording (`make eval-ci-record` after an
intended change).

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
