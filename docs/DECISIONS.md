# Decisions

Running log of design choices: what was chosen, what was rejected, and why.

## 2026-10-01 · A0 · Python + uv workspace

- **Chose:** Python 3.12, a uv workspace with shared code in `libs/`.
- **Rejected:** Poetry (slower, and its monorepo support is weaker); Go (slower to iterate on, and
  the ML and agent ecosystem is in Python).
- **Why:** fast installs in CI and one lockfile for every service.

## 2026-10-01 · A0 · Contracts as Pydantic models, JSON Schema generated

- **Chose:** Pydantic v2 models in `libs/contracts` are the source of truth. JSON Schema files are
  generated into `docs/schemas/`, and a test fails when they drift.
- **Rejected:** handwritten JSON Schema (it drifts from the code).
- **Why:** one definition is used for API validation, the replayer, the detector and agent output
  checks, and non-Python consumers still get a language-neutral schema.

## 2026-10-01 · A0 · Telemetry event design

- **Three event types in one batched endpoint**, discriminated by `event_type`: `gpu_metrics`
  (periodic DCGM-style sample), `xid` (driver error), `bmc_log` (fans, PSUs, thermals). A union
  keeps a single ingestion path and a single Kafka topic, and adding a type is a non-breaking change.
- **Client-generated `event_id` (UUID)** so retries can be deduplicated (idempotency in A1/A2).
- **Timezone-aware timestamps are required.** Naive timestamps are rejected, because mixed
  timezones across a fleet silently break time-window joins.
- **Error counters are cumulative** (`*_total`), the way NVML/DCGM report them. Detectors diff
  them. Sending deltas would lose data when an event is dropped.
- **Unknown fields are rejected** (`extra="forbid"`), so a misspelled field fails loudly instead
  of being dropped silently. This is a trade-off: clients must upgrade with the schema.
  `schema_version` is pinned to `"1.0"`.

## 2026-10-01 · A0 · Incident record design

- **The detector creates an incident with ≥1 evidence item; the agent adds a `diagnosis`.**
  A diagnosis may only reference evidence IDs that exist on the incident, so the agent can't
  invent evidence.
- **`FailureType` is shared** by the fault injector's ground truth, the detector's suspected type
  and the agent's root cause, so A3 and A6 can score them with exact matches.
  `noisy_neighbor` is a valid root cause: concluding "not a hardware fault" is a correct answer.
- **The approval requirement is derived from the action type** (`reset_gpu` and `drain_node` change
  state), not set by the model, so the agent can't skip approval.

## 2026-10-01 · A0 · BMC logs normalized to Redfish `LogEntry`

- **Chose:** `bmc_log` follows the DMTF Redfish `LogEntry` shape: `entry_type` (event/sel/oem),
  `severity` (ok/warning/critical), registry `message_id` + `message_args`, and for sensor
  entries `sensor_type`, `sensor_number` and `entry_code` (assert/deassert). `source_format`
  records whether it was collected via Redfish, IPMI SEL or syslog.
- **Rejected:** raw IPMI SEL (binary, legacy, often disabled over LAN on new platforms); one
  vendor's format such as iDRAC Lifecycle Log (ties us to one vendor); free-text syslog (needs
  parsing before it can be used).
- **Why:** Redfish is the current standard across Dell, HPE, Lenovo, Supermicro, OpenBMC and
  NVIDIA HGX/DGX BMCs, and it already carries SEL records as `EntryType: SEL`. `message_id` gives
  the detector and RAG an exact key to match on, and assert/deassert pairs show how long a fault lasted.
  Values are snake_case versions of the Redfish enums; the collector or replayer does the mapping.

## 2026-10-01 · A0 · OpenAPI generated from a FastAPI app

- **Chose:** the ingest service's routes, with real request and response models, define the API;
  `docs/schemas/ingest.openapi.json` is exported from them and checked for drift in tests. The
  handler is a placeholder until A1.
- **Rejected:** writing the OpenAPI YAML first and generating server stubs (two sources of
  truth, and generated FastAPI stubs are awkward to maintain).
- **Why:** same reasoning as the schemas: one definition, and the published spec can't drift.

## 2026-10-01 · A0 · Ingestion API shape

- **`POST /v1/telemetry` returns 202, not 200**: events are queued (Kafka in A2), not stored yet.
- **The response reports `accepted` and `duplicates`**, so clients can confirm that retries were deduplicated.
- **API key in an `X-API-Key` header**, from `INGEST_API_KEYS`, compared in constant time.
  It fails closed: with no keys configured, every request is rejected. Rejected: OAuth/mTLS for
  now (too much for a local demo); A8 can put this behind a gateway.
- **429 with `Retry-After` is part of the contract from day one**, so the replayer is built to
  back off before A1 implements the throttling.
- **Errors use one `{error, message}` body**; 422 keeps FastAPI's validation format, which
  lists every field that failed.
