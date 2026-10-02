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

## 2026-10-01 · A3 · Fault injection changes physics, not numbers

- Faults change the simulator's inputs (cooling, enforced power cap, utilization, error-counter
  rates, a GPU going silent) and emit the events real hardware would (XID 48/74/79, Redfish fan,
  PSU and PCIe entries). Thermal throttling was added to the GPU model for this. Signatures then
  follow from physics. For example, a dead fan only shows up in temperatures if the GPU is busy.
- **BMC dropout (50% in evaluation):** without it every fault announced itself through a log entry
  and the layer comparison was meaningless. Real BMC logs often come through a separate,
  lossy pipeline. Driver XIDs are kept reliable.
- Each evaluation run uses its own node-name prefix, so runs share the database without deletes.

## 2026-10-01 · A3 · Three detector layers, measured separately

- **Chose:** static thresholds and event rules → per-GPU EWMA z-scores → a thermal residual model.
  Each is a config flag so its effect can be measured on identical data.
- **Thermal residual model:** temp ≈ a_gpu + b_model × power, where power is filtered with the
  ~90 s heatsink time constant (without that, every job end looks like overheating for three
  minutes). The slope is fitted per GPU model across the fleet and the intercept per GPU. The model
  only learns from healthy minutes, so a fault doesn't become the new normal.
- **Rejected:** Isolation Forest / learned models for now. With labelled faults and clear
  physics, an explainable model that technicians can check ("12 °C hotter than its power
  explains") beats a black box, and it already reaches 100% precision. Revisit with real data.
- **Rejected:** z-scores as an alarm source. They produced 309 false alarms in 3 runs; they're
  only useful together with a model that explains workload.

## 2026-10-01 · A3 · Incidents: grouping, classification, timing

- Alerts group into one incident per (node, related component) within 20 min; an incident resolves
  after 30 min of quiet. Classification is an ordered rule list over alert kinds (e.g. XID 79 or a
  missing GPU → gpu_off_bus beats anything else). It's simple, explainable, and 71/71 correct.
- `opened_at` = first actionable (sev1–3) alert, which is what time-to-detect measures. Metric
  alerts are stamped at the end of their minute, when the data actually exists.
- Incident IDs are deterministic (uuid5 of run, node, GPU, kind, time), so re-processing upserts
  instead of duplicating. The live detector replays 3 h of history at startup without creating copies.
- **Evaluation discipline:** two seeds for development, a third held out until the end. Three bugs
  were found by the evaluation, not by unit tests (see eval/reports/detection.md).

## 2026-10-01 · A4 · Knowledge base: corpus, chunking, storage

- **Corpus written for this project:** 22 runbooks, one per failure type plus procedures (drain,
  reset, RMA, DCGM, BMC triage, severity...), and 64 seeded synthetic past incidents. Vendor docs are
  linked, not copied, to stay clear of licensing questions.
- **Chunking:** runbooks by `##` heading (each chunk starts "title > heading" so it reads on its own,
  and citations point at the exact section); past incidents stay whole, because splitting them
  separated "root cause" from the node and symptoms it belongs to. Chunk IDs are stable
  (`doc#heading`), so citations and eval labels survive re-ingestion.
- **pgvector in the existing TimescaleDB** (HNSW, cosine) plus a generated `tsvector` column.
  Rejected: a separate vector database. One Postgres means one backup, one access-control model, and
  joins with incidents later.
- **Local models (fastembed / ONNX):** bge-small-en-v1.5 embeddings and an ms-marco MiniLM
  cross-encoder. No PyTorch, no per-query cost, and nothing leaves the machine. Rejected for now:
  hosted embedding/rerank APIs (cost and data egress for a corpus this small).

## 2026-10-01 · A4 · Retrieval and grounded answers

- **Hybrid with reciprocal rank fusion, then rerank:** measured 73% → 83% → 100% recall@5. RRF was
  chosen over weighted score blending because it needs no calibration between score scales.
- **Keyword query ORs the terms** (`websearch_to_tsquery` ANDs them, which returns nothing for most
  natural questions); `ts_rank_cd` still rewards chunks matching more terms.
- **Refusal in two layers:** a relevance gate on the reranker score (no model call when retrieval is
  weak), and post-hoc citation validation (citations must point at provided chunks; an answer with no
  supported claim becomes "not enough information"). The model can't cite what it wasn't shown.
- **Answers with Claude** (`claude-opus-5-5`, effort `medium`, JSON-schema structured output, cached
  system prompt, server-side refusal fallback, 60 s timeout with SDK retries). Without an API key the
  service answers **extractively** (the best passage, cited, labelled `provider: extractive`), so the
  API works today and switches to Claude when a key is added. Model failures degrade to extractive
  instead of failing the request.
- **Bug found by tests:** chunk IDs contain `#`, which starts a URL fragment, so citation links
  silently truncated. URLs are now percent-encoded.

## 2026-10-01 · A5 · Investigation agent and MCP tools

- **Open-source model, run locally:** Qwen3 30B-A3B (mixture of experts, ~3B active parameters per
  token) through Ollama on the host, behind an OpenAI-compatible provider. No per-token bill and no
  data leaves the machine; the same provider class talks to vLLM in B5. Rejected for now: a hosted
  API as the default (cost per investigation, and the results would measure a vendor's model, not
  the system). Claude stays available behind `AGENT_PROVIDER=anthropic`.
- **Ollama on the host, not in Compose:** Docker on macOS has no GPU access, so a containerized
  model would run on CPU. The agent container reaches it at `host.docker.internal`; Ollama listens
  on 127.0.0.1 only.
- **Context pinned to 32k** (`deploy/ollama/Modelfile`): past Ollama's default context, input is
  dropped silently, which would hide tool results from the model. The loop also wraps up when one
  call's prompt passes 26k tokens.
- **One tool registry, served twice:** the agent loop calls tools in-process; `agent mcp` serves the
  same Pydantic schemas and validation over MCP (low-level `Server`, so the schemas are ours, with
  read-only/destructive hints). Rejected: the loop as an MCP client of its own server (a subprocess
  and a serialization hop per call, for no isolation gain on one machine).
- **Flat tool schemas:** `$ref`s inlined and enums spelled out; small local models fill nested
  references poorly.
- **Diagnosis through a `submit_diagnosis` tool**, not provider JSON mode: tool calling is the one
  structured-output path every provider has. Arguments are checked against the schema and the run:
  evidence IDs must be on the incident, citations must be chunks a tool returned in this run, a
  non-`unknown` cause needs at least one of each. Problems go back to the model; 2 retries.
- **Tool calls written as text are recovered**, narrowly: Qwen3 under Ollama sometimes writes
  `{"name": ..., "arguments": {...}}` into its message instead of the tool-call channel, and the
  first batch run stalled on it (3 nudges, then `invalid_output`). A message that is *only* such
  calls (bare, in `<tool_call>` tags, a JSON fence, or with one orphan tag left over after the
  server consumed the other, which the second batch hit), naming tools offered on that turn, becomes
  real calls; anything else stays text. Recovered calls get `text_` IDs, so the trace and the
  report count them.
- **Budgets:** 12 model calls, 200k tokens, 300 s per model call, 20 s per tool call, 20 min per
  run. Near a limit the model is offered only `submit_diagnosis`. A tool failure or timeout becomes
  an error result, not a failed run.
- **Approvals enforced by the database, not by prompts:** `drain_node` and any drain/reset
  recommendation only insert a pending row. `incident_agent` can INSERT requests but not UPDATE
  them; `approval_service` can UPDATE only the decision columns and can't INSERT. A trigger makes
  a decision final, keeps the request immutable, and rejects a decider named `agent*` or equal to
  the requester. The API needs a separate approver key to decide.
- **Diagnosis stored on the run (`agent_runs.diagnosis`), not in `incidents.record`:** the detector
  owns incident rows and rewrites them on every update, which would drop an agent-written field.
- **Traces in Postgres:** `agent_runs` + `agent_steps`, one row per model or tool call with
  input, output, tokens and latency, written as they happen (a crashed run still leaves its trace).
  A6 scores from these tables. Reasoning text is kept in the trace and never sent back to the model.
- **Cost from a price table:** USD per million tokens per provider; zero for the local model, so
  `cost_usd` is 0 by construction and tokens and latency are the numbers to watch.
- **Watcher investigates new live incidents** (sev1–3, once each), one at a time: a local model
  serves one request at a time anyway.

## 2026-10-01 · A6 · Evaluation harness

- **Sets built from injected faults, one case per fault.** Labels come from the injector's ground
  truth plus a per-type policy read off the runbooks' remediation sections (`POLICY` in
  `harness/src/evaluation/sets.py`): the runbook to cite and the actions it allows. Example: after
  XID 79 only a drain is allowed, because the runbook says a GPU reset is not enough.
- **Decoys added from decoy-only replays.** The detector correctly opens no incident for most
  noisy-neighbor decoys, so a plain replay left the held-out set with none and the unsafe-action
  rate unmeasurable. The decoys that *do* reach the agent (the hard ones) fill each set to 25.
- **dev / held-out test, 25 each, new seeds** (51/61 dev, 53/63 test, 7 CI). Rounds are tuned on
  dev only; test is scored at the start and the end. Smaller than the plan's 50-100 because the
  local model takes ~2.5 min per incident; noise is measured by running the baseline twice.
- **Missed actions are a first-class metric**, next to unsafe actions: a real fault answered with
  "none" or "monitor" stays in service. The A5 baseline showed this is the agent's main failure.
- **Fresh in-memory approvals per eval case.** With the real table, a drain filed in one round
  appears in the next round's `get_node_inventory`, and the agent would see its own earlier
  request.
- **Judge: Gemma 3 12B**, a different family from the agent, scores only citation support.
  Calibrated against blind hand grades (41 pairs, 20 incidents; graded by Claude as a stand-in,
  by the user's choice). v1 was lenient (kappa 0.31; 7 of 9 disagreements "supported"). v2 was
  written from the disagreements on incidents 1-10 only and measured on 11-20: kappa 0.00 to 0.60.
  A rule suggested by a held-out case was deliberately left out of v2.
- **CI replays recorded model replies** through the real stack. Timestamps in recorded tool
  arguments are stored as offsets from the incident's first signal, because CI replays the data
  relative to "now". The gate fails on a failed run, more tool errors, or lower scores than the
  recording.
- **Reports are tagged** with the commit, a dirty flag, the model, a hash of the system prompt and
  the judge version, so every number in RESULTS.md can be traced to the code that produced it.
- **Rounds chosen from traces, not guesses.** The baseline traces showed the model *seeing* a
  power-limit cut or a thermal residual and dismissing it under the prompt's "no hardware errors"
  rule, so rounds 2 and 3 changed exactly that guidance. Retrieval got no round, but the reports
  show where it should go next: power faults are diagnosed correctly yet cite the power-capping
  policy (rb-015) and BMC triage (rb-013) instead of the power-fault runbook (rb-003), in all 4 dev
  and 2 test cases where the cause was right but the runbook was not cited (the 3rd test case is a
  decoy citing the thermal runbook).
- **Prompt guidance phrased generally, not as the eval's answer key.** Round 3 says "take the
  action from the runbook's remediation", not "drain for thermal, replace for power", so the
  held-out gains measure following runbooks, not memorizing labels.
- **Round 4 rejected despite halving latency.** The instruct model needs twice the tool calls, so
  it uses 2.3x the tokens: faster on a Mac (prefill is cheap), more expensive on any per-token API,
  and worse on actions. Latency on this hardware is not the same as cost.
- **The CI recording is made with the final agent**, after the rounds; the baseline recording was
  skipped on purpose, because the gate compares a PR against the recorded agent.
- **Replays made deterministic for the CI gate.** The first gate runs failed on an unchanged agent:
  the replay started "now minus a day" to the second, so samples landed in different 1-minute
  buckets; alerts within a minute were numbered in production order; and two replays of the same
  nodes overlapped in time. Now replays start on a whole minute, alerts sort on every field, and
  each gate run uses a fresh run id (recordings template the run prefix). Two replays of the same
  seed give 24/24 identical incidents. The eval also waits for the 1-minute rollup to cover a
  fresh replay, because get_metrics read it empty in the first minute.

## 2026-10-02 · A7 · Web UI and Slack bot

- **Accounts with roles, not typed names.** The A5 review accepted that `decided_by` was whatever
  an approver-key holder typed. Now people log in (scrypt hashes, server-side sessions storing
  only a token hash), decisions are recorded under the session's username, and the
  approver-key endpoint is gone. Roles: viewer, approver, admin. A check constraint keeps any
  account from being named like an agent, matching the approval trigger.
- **Audit log written by a trigger**, append-only (another trigger refuses UPDATE and DELETE, even
  for the superuser), so no code path can approve without leaving a record.
- **One API for both front ends.** The Slack bot has no database write path for decisions: it
  calls the same people routes as the browser, with its own key plus the clicking user's Slack
  id, and only linked accounts can act. Roles, audit and feedback behave identically in both.
- **React + Vite + Tailwind, served by nginx** (not Next.js): the API already exists, so a static
  SPA is the smallest thing to run and secure. nginx exposes only `/api/v1/(auth|ui)/` from the
  agent; the machine API, `/metrics` and `/docs` aren't reachable through the UI origin.
- **CSRF**: session cookie is HttpOnly and SameSite=Strict, and state-changing requests need a
  custom header a cross-site form can't send. No CORS at all (same origin).
- **Follow-up questions reuse the agent loop** (refactored around a `Task`: prompt, finish tool,
  checker) with the write tool removed: same budgets, citation checks and traces, stored as runs
  of kind `followup`. The investigation prompt and messages are unchanged, so A6's eval and the
  CI recording stay valid.
- **Slack in Socket Mode**: an outbound WebSocket, so the localhost-only stack needs no public
  URL or inbound firewall rule. Built and tested against a fake Slack client plus the real API
  (in-process); connecting a workspace only needs the tokens.
- **Screenshots come from a script** (`web/scripts/screenshots.mjs`, Playwright) that walks the
  whole flow: login, a real investigation, approval, audit, follow-up, feedback, and a viewer
  who must not see approval buttons. It doubles as an end-to-end check.
- **What the end-to-end browser run caught in follow-up chat** (fixed before merge):
  1. A follow-up answer said "this node had 5 prior thermal incidents"; 3 were this node (named
     without the eval's run prefix) and 2 were other nodes. The follow-up prompt now says to
     check the node named in each past report before attributing it. A wrong answer stays in
     that incident's chat history and the model repeats it, so history can carry an early
     mistake forward; the thumbs-down feedback is the way to flag it.
  2. The model often ends a chat turn in prose (sometimes with a hand-written "citations:" line)
     instead of calling `submit_answer`. For follow-ups only, a plain-text final reply is now
     accepted as the answer, with no sources: claimed citations are dropped because they were
     never checked. Investigations still require the structured, validated submission.
  3. A third text form of tool call (the tool name on one line, then the arguments object) is
     recovered like the other two.
