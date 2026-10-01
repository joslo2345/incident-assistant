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

## 2026-10-01 · A0 · Tooling, CI and local stack

- **Makefile as the single entry point** (`make check`, `make up`). CI runs the same commands, so
  "green locally" means green in CI.
- **Pre-commit** runs ruff, mypy and the schema drift check before each commit. The slower tests
  run in CI only.
- **Dockerfile:** two stages with uv, dependencies installed before the code is copied (better
  layer caching), runs as a non-root user, and has a health check on `/healthz`. The build context
  is the repo root so services can install `libs/contracts`.
- **Compose file in `deploy/`** with only the services that exist so far. A2 adds Redpanda,
  TimescaleDB, Prometheus and Grafana, rather than placeholder containers that do nothing.
- **GitHub Actions:** one check job plus a Docker build matrix over services. Permissions are
  read-only and the Docker build cache is stored in GitHub Actions. Rejected: pushing images to a
  registry now (no consumer until A8).

## 2026-10-01 · A1 · Workload source: Alibaba GPU trace, busiest machines with job churn

- **Chose:** Alibaba cluster-trace-gpu-v2020. A one-time `replay-prepare` step builds a 24 h
  slice: the busiest trace day, then the 8 eight-GPU machines with the most jobs among those
  averaging ≥40% utilization. Multi-GPU instances (util > 100%) are split across neighboring GPUs.
  The 6.7 KB slice is committed (CC BY 4.0, attributed in its `.meta.json`), so nobody needs the
  1 GB download.
- **Rejected:** Microsoft Philly traces (less per-job GPU detail); ranking machines only by
  GPU-seconds (it picked machines running one 8-GPU job all day: flat 94% utilization and
  no dynamics to detect anomalies against).
- **Known limit:** the trace has lifetime-average utilization per instance, not time series. Within a
  job, variation is synthetic (AR(1) wander). Results in A3 must not be read as "real job dynamics".

## 2026-10-01 · A1 · Telemetry synthesis

- **Mixed fleet (5 H100 SXM, 3 A100 SXM)** in `replayer/fleet.toml`. The trace's 2020 V100 load
  is mapped onto modern GPU profiles; the load pattern is real, the hardware numbers are profiles.
- **Physically consistent model** so faults in A3 have realistic signatures: power follows
  utilization and caps at the limit (setting the throttle flag), temperature follows power with a
  90 s lag, clocks drop when power-capped, and ECC counters are cumulative with rare correctable errors.
- **Benign BMC noise:** about one inlet-temperature warning per node per day, which asserts
  and then clears. These give A3 something that looks alarming but isn't a fault.
- **Seeded** for reproducible values; event IDs are random (uuid4) so separate runs never collide in dedupe.

## 2026-10-01 · A1 · Contract change: optional `gpu_model` on `gpu_metrics`

- Needed for a mixed fleet: baselines (power limit, normal temperature) differ by model, and the
  detector shouldn't need a separate inventory lookup to know that. It's optional and additive,
  so existing clients aren't broken. It uses DCGM's `modelName` values.

## 2026-10-01 · A1 · Ingest idempotency and backpressure

- **Dedupe across batches:** an in-process cache of recent event IDs (10 min TTL, 1M max).
  It's best-effort: it doesn't survive restarts or span replicas. The real guarantee is idempotent
  inserts on `event_id` in A2. Rejected: Redis now (another service only to get a guarantee
  the database gives us for free).
- **Backpressure:** accepted events go into a bounded buffer (50k events) behind a sink interface.
  If a batch doesn't fit, it gets 429 with `Retry-After`, and **its IDs aren't remembered**, so the
  retry isn't mistaken for a duplicate. A batch made only of duplicates is never throttled.
  In A2 the sink becomes a Kafka producer.
- **Replayer retries** 429 (honoring Retry-After), 5xx and network errors with exponential
  backoff and full jitter, always resending the identical batch. It doesn't retry 401/422.

## 2026-10-01 · A2 · 202 means durable in Kafka

- **Chose:** the ingest API awaits Kafka acknowledgement (`acks=all`, idempotent producer) for
  every event before answering 202. Backpressure became a limit on events in flight (429), and a
  Kafka failure returns 503 with `Retry-After`; the replayer already retries both.
- **Rejected:** A1's in-memory buffer drained in the background. It was faster (65k vs 34k
  events/s), but a crash of the API process lost events that clients had been told were accepted.
- **Why:** "accepted" should mean "will be stored". Halving peak throughput still leaves about 50x
  headroom over the fastest replay we use.

## 2026-10-01 · A2 · At-least-once consumer, idempotent inserts

- **Chose:** the consumer commits Kafka offsets only after the database transaction commits. Rows go
  in by COPY to a temp table, then `INSERT ... ON CONFLICT DO NOTHING` on a unique `(event_id, time)`
  index. A crash anywhere means redelivery, and redelivered rows are no-ops.
- **Rejected:** Kafka transactions / exactly-once semantics (they don't cover the database write
  anyway); auto-commit (it can commit before the write and lose data).
- **Why:** this is the standard pattern and it's provable. Integration tests SIGKILL the consumer
  mid-replay and force redelivery by rewinding offsets; both end with rows = accepted events.
- **Database errors** are retried indefinitely without committing (falling behind beats losing
  data). **Invalid messages** go to `telemetry.dlq` with the validation error as a header, so one bad
  event can't block a partition.
- With this in place, the API's in-memory dedupe cache is just an optimization that keeps
  duplicates off the stream.

## 2026-10-01 · A2 · TimescaleDB layout

- **One hypertable per event type** (`gpu_metrics`, `xid_events`, `bmc_log`). A wide typed table
  for metrics, rather than a generic (name, value) table, keeps queries and compression simple.
- **Rollups:** `gpu_metrics_1m` (continuous aggregate) and `gpu_metrics_1h` (hierarchical, built on
  the 1m one with sample-weighted averages). Both have real-time aggregation on.
- **Late data bug, found and fixed (migration 003):** the refresh window was 2 hours, so backfilled
  or late data never reached the rollups. Real-time aggregation only covers data *newer* than
  the watermark. Widened the window to 14 days (= raw retention); refreshes only recompute changed
  buckets. Lesson: test with late data, not just "now".
- **Compression** after 2 days (segmented by GPU); **retention**: raw 14 days, 1m 90 days,
  1h 2 years, XID/BMC 1 year.
- Migrations are plain SQL files applied in order by a one-shot `migrate` container (with an
  advisory lock for concurrent starts). Applied migrations are never edited; fixes go in new files.

## 2026-10-01 · A2 · Observability

- **Prometheus metrics** in both services: request rate/latency, events accepted/duplicate,
  Kafka ack time, in-flight events, consumer lag, rows inserted/duplicate, DB write time,
  end-to-end latency, DLQ and DB-error counts. Redpanda's own metrics are scraped too.
- **Dashboards as code:** `scripts/build_dashboards.py` generates the Grafana JSON. Panels are
  defined once in Python instead of hand-edited JSON. Every panel query was checked through
  Grafana's query API against live data.
- **ECC panels count increases between consecutive buckets** (like Prometheus `increase()`), so
  a counter reset after a driver reload isn't shown as new errors or as negative errors.
- **Known gap:** murmur2 hashing of 8 node IDs into 8 partitions leaves some partitions empty and
  doubles up others. That's fine at this scale; with more nodes the spread evens out.

## 2026-10-01 · Security review before A3

- **Found and fixed a critical issue:** Grafana's anonymous viewer access plus a superuser
  datasource let anyone who could reach port 3000 run arbitrary SQL as the Postgres superuser,
  and every port was reachable from the LAN. Both were verified, then fixed (see `SECURITY.md`).
- **Chose defense in depth over a single fix:** localhost-only ports, no anonymous access, AND
  least-privilege DB roles, AND escaped template variables. Each layer alone would have stopped it.
- **Chose generated per-machine secrets** (`make env`) over documented defaults: Compose refuses
  to start without them, so a default password can't slip through.
- **CI now gates on security:** gitleaks, pip-audit, Trivy config and image scans (fixable
  HIGH/CRITICAL fail the build). This moves part of A8 earlier because it was cheap and caught real issues.
- Lesson for the case study: the convenient demo settings (anonymous dashboards, default passwords,
  `0.0.0.0` ports) combined into a remote-code-execution path that no single setting showed.
