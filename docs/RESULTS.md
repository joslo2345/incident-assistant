# Results

Measured numbers, one section per package. Each row says how it was measured and on what.

## A0 · Foundation

| Metric | Value | How measured |
| --- | --- | --- |
| Ingest image size | 259 MB | `docker image ls`, python:3.12-slim base, arm64 (MacBook) |
| Test suite | 40 tests, 0.3 s | `make test` locally |

## A1 · Telemetry replay and ingestion

Setup: Apple M3 Pro (12 cores), Docker Desktop. Ingest runs in its container (one uvicorn worker);
the replayer runs on the host. Fleet: 8 nodes × 8 GPUs (5 H100, 3 A100), one sample per GPU every 10 s.

| Metric | Value | How measured |
| --- | --- | --- |
| Sustained ingest throughput | **~65k events/s** | `replay --duration 3d --speed 0 --batch-size 500 --concurrency 4`: 1.66M events in 25.6 s |
| Short-run throughput | 68–75k events/s | 6 h of simulated data (138k events), batch 500/1000, concurrency 4/8 |
| Loss / duplicates | 0 / 0 | accepted = sent across every run; no retries needed |
| Ingest CPU at that rate | ~78% of one core | `docker stats` during the run (near single-worker saturation) |
| Ingest memory | ~250 MB | mostly the 1M-entry event_id dedupe cache |
| Replayer generation rate | ~154k events/s | events synthesized + validated, no network |
| Headroom vs this fleet | ~10,000× real time | fleet produces 6.4 events/s at 1x |

Takeaways: the API isn't the bottleneck for any realistic replay speed (100x = 640 events/s). Batch
size above 500 and concurrency above 4 didn't help; both client and server are single-core
Python processes. More throughput would come from more uvicorn workers, which makes the
in-process dedupe cache per-worker, one more reason the real idempotency belongs in the consumer (A2).

## A2 · Storage, streaming and observability

Setup: same laptop; full Compose stack (Redpanda 1 node, TimescaleDB 2.30/PG17, ingest with one
uvicorn worker, one consumer). Latency comes from per-row `ingested_at` (Kafka timestamp, set when
ingest publishes) and `inserted_at` (DB commit), as percentiles over every row of the run.

| Metric | Value | How measured |
| --- | --- | --- |
| Publish → DB commit, realistic load | **p50 10 ms, p95 19 ms, p99 21 ms** | 640 events/s (100x replay), 46k rows |
| Ingest request (incl. Kafka ack) | p50 15 ms, p95 24 ms | Prometheus `ingest_request_seconds` over the same run |
| **API → DB, realistic load** | **≈ 25 ms p50, ≈ 43 ms p95** | sum of the two above |
| Publish → DB commit, max load | p50 43 ms, p95 72 ms, p99 268 ms | 553k events at max speed |
| Sustained ingest throughput, durable | **~34k events/s** | 553k events in 16.3 s, 202 only after Kafka acks |
| Throughput vs A1 (in-memory) | 65k → 34k events/s | the cost of waiting for `acks=all` before 202 |
| Consumer kill (SIGKILL) mid-replay | **0 lost, 0 duplicated** | integration test: 553k events, rows = accepted |
| Forced redelivery (offset rewind) | all redelivered rows skipped, 0 new rows | integration test: `consumer_rows_total{result="duplicate"}` |
| Invalid message | lands in `telemetry.dlq` with the validation error in a header | integration test |
| Full 14-day rollup refresh | 3.6 s for 287k minute-buckets | first run of the widened policy (migration 003) |

Takeaways: at a realistic fleet rate, data is queryable about 25 ms after the API receives it. Under
maximum load the p99 tail (268 ms) comes from consumer batching (up to 5000 messages or 500 ms);
smaller batches would cut it at some throughput cost. Durability halved peak ingest throughput, which
still leaves about 50x headroom over the 100x replay rate.

## A3 · Anomaly detection

Full report: [eval/reports/detection.md](../eval/reports/detection.md). Three 48 h replays, 72
injected faults plus 12 noisy-neighbor decoys, half with their BMC logs dropped; one run was held out.

| Metric | Value | How measured |
| --- | --- | --- |
| Recall (final detector) | **99%** (71/72) | faults with a matching alarm, pooled over 3 runs |
| Precision | **100%** (73 alarms, 0 false) | alarms matching a real fault |
| Noisy-neighbor decoys raising an alarm | **0/12** | v3; naive z-scores flagged 8/12 |
| Correct failure type | 71/71 | earliest matching alarm's type |
| Time to detect, median | 0.1 min (off bus) to 4.9 min (thermal); ECC 77 min | ECC ramps over hours and is caught ~1 h before the DBE |
| Naive z-scores, precision | 21% (309 false alarms) | the "before" of the improvement |
| Improvement from the thermal residual model | precision 21% → 100%, recall kept at 99% | v2 vs v3 on the same data |
| Detector speed | 2,880 minutes of fleet data in ~2.5 s | backfill, 64 GPUs |
| CI quality gate | recall ≥ 83%, precision ≥ 90%, 0 decoy alarms | 1-day replay per PR (28 s) |

## A4 · Knowledge base and RAG

Full report: [eval/reports/retrieval.md](../eval/reports/retrieval.md). 150 chunks (22 runbooks,
64 past incidents); 30 answerable + 6 unanswerable questions.

| Metric | Value | How measured |
| --- | --- | --- |
| Recall@5, vector only | 73% | correct chunk in top 5 |
| Recall@5, hybrid (vector + keyword, RRF) | 83% | same |
| **Recall@5, hybrid + rerank** | **100%** (hit@1 80%, MRR 0.87) | same |
| Unanswerable questions refused | **6/6**, with 0/30 answerable refused | reranker relevance gate at −0.4 |
| Search latency p50 | 9 ms hybrid, 235 ms with rerank | CPU, local models |
| Ingestion | 150 chunks embedded in 4.1 s; re-run embeds 0 | content-hash incremental sync |
| Retrieval cost per query | $0 | local ONNX models (fastembed) |

## A5 · Investigation agent

Full report: [eval/reports/agent-baseline.md](../eval/reports/agent-baseline.md). Every actionable
incident of detector run `ev3-v3-residual` (26, all matched to an injected fault, 13 with BMC logs
dropped), investigated once each by Qwen3 30B-A3B running locally (Ollama, Apple M3 Pro, 36 GB).
This is a baseline; A6 builds the full eval and the improvement rounds.

| Metric | Value | How measured |
| --- | --- | --- |
| Runs ending in a valid, cited diagnosis | **26/26** | status `succeeded`; every citation is a chunk a tool returned in that run |
| **Root-cause accuracy** | **18/26 (69%)** | agent's `root_cause` vs injected fault type |
| Root-cause accuracy, BMC logs dropped | 6/13 | same, on the faults with no BMC entries |
| Detector rule classification, same incidents | 26/26 | the bar the agent has to reach |
| Cites the runbook for the true failure type | 17/26 | cited `doc_id` is that type's runbook |
| Latency per investigation | p50 **126 s**, p95 187 s | wall clock; tool calls take ms, the model takes 20–50 s per turn |
| Model calls / tool calls per run | 4.3 / 3.3 | means |
| Tokens per investigation | 22.8k (input + output, all calls) | ~65% of output is the model's reasoning |
| **Cost** | **$0** | self-hosted; electricity aside |
| Tool calls recovered from text | 10 | model wrote the call as text (see DECISIONS) |
| Invalid tool arguments, corrected by the model | 12 | e.g. `search_runbooks` without `query`; all retried successfully |
| End-to-end on a fresh injected fault | correct, cited, drain filed as pending | `tests/integration/test_agent.py` |
| Approvals: agent cannot decide, self-approve, or edit a request | enforced by DB roles and trigger | integration test |

**What went wrong, and why (input for A6 round 1):** 7 of the 8 misses are faults labelled
`noisy_neighbor` (5 thermal or power faults with BMC logs dropped, 1 power fault, 1 PCIe), each with
"none" or "monitor" as the action, so these are missed faults, not false alarms. The traces show the
model *saw* the signals (power limit cut from 400 W to 240 W; a +17 °C thermal residual at 88.5 °C;
PCIe replay spikes) and dismissed them, citing the prompt rule "without hardware errors … that is
noisy_neighbor". The prompt never says that an enforced power-limit drop or a large thermal residual
is itself a fault signal. Fixing the prompt is the first measured change in A6, against this
baseline.

**Run-to-run variance:** the same incident (`ev3-h100-node-05` thermal) was diagnosed correctly in
one batch and as `noisy_neighbor` in the next, at temperature 0.2. Single runs are noisy; A6 should
repeat each incident and report spread.

## A6 · Evaluation harness and improvement rounds

`make eval` scores the agent on a labelled set in one command (reports in
[eval/reports/a6/](../eval/reports/a6/), each tagged with commit, model, prompt hash and judge).
Sets: `dev` 25 and held-out `test` 25 incidents (all 7 types, 3 decoys each, about half with BMC
logs missing), plus a 10-incident `ci` set. Local model Qwen3 30B-A3B on an Apple M3 Pro; judge
Gemma 3 12B. Cost is $0 throughout.

**Held-out test set, before and after** (scored only twice):

| Metric | Baseline | Final (round 3) |
| --- | --- | --- |
| Runs with a valid diagnosis | 25/25 | 25/25 |
| **Root-cause accuracy** | 19/25 (76%) | **22/25 (88%)** |
| Root cause, BMC logs missing | 9/14 | 12/14 |
| **Action allowed by the runbook** | 15/25 (60%) | **23/25 (92%)** |
| **Missed actions** (real fault left in service) | 8/22 | **1/22** |
| Unsafe actions (hardware action on a decoy) | 0/3 | 0/3 |
| Cites the runbook for the true type | 16/25 | 19/25 |
| Citation support (judge v2) | 89% | 88% |
| Latency p50 | 145 s | 125 s |
| Tokens per incident | 24.0k | 27.5k |
| Cost per incident | $0 | $0 |

Thermal runaway, the worst type at baseline, went from 1/4 to 4/4 on the held-out set.

**Improvement log (dev set, one change per round):**

| Round | Change | Root cause | Action allowed | Missed actions | Unsafe | Latency p50 | Verdict |
| --- | --- | --- | --- | --- | --- | --- | --- |
| r0 | Baseline (A5 as merged), run twice | 19, 18 | 15, 18 | 9, 7 | 0, 0 | 158 s, 178 s | 5 of 25 cases flip between identical runs: a change must move a metric by more than ~3 to count |
| r1 | Never send null assistant content (a run crashed when reasoning used the whole token budget) | 17 | 17 | 7 | 0 | 163 s | Kept: the crash case recurred and the run survived; no quality effect, as expected |
| r2 | Prompt: power-limit drops, thermal residuals and rising error counters are faults on their own; missing BMC logs aren't evidence of health | **22** | 19 | 5 | 0 | 143 s | Kept: BMC-missing faults 8-9/13 to 13/13; power faults 2/4 to 4/4; no decoy got a hardware action |
| r3 | Prompt: take the action from the runbook's remediation; monitor/none only for workload or genuine doubt | 22 | **24** | **1** | 0 | 124 s | Kept: missed actions 5 to 1 with no unsafe actions |
| r4 | "Cheaper model": the instruct (non-thinking) Qwen3 30B-A3B | 21 | 18 | 5 | 0 | **61 s** | Rejected: twice as fast, but 2 runs ended without a diagnosis, actions regressed, and tokens went up 2.3x (twice the tool calls, each resending the context), so it would cost *more* on a per-token API |

All rounds: 0/3 unsafe actions. Citation support moved between 83% and 97% with no pattern (it
moved 13 points between the two identical baseline runs), so it isn't used to pick rounds.

**Next round, not run:** every real fault with the right root cause but not the right runbook is a power
fault citing rb-015/rb-013 instead of rb-003, a retrieval ranking issue for a retrieval round.

**Judge calibration** ([eval/judge/calibration.json](../eval/judge/calibration.json)): 41
(diagnosis, cited passage) pairs from 20 dev incidents, hand-graded blind to the judge. The hand
grades were made by Claude as a stand-in for a human grader (the user's choice), so they measure
agreement with a careful second grader, not with an operator.

| Judge prompt | Agreement | Cohen's kappa | Errors |
| --- | --- | --- | --- |
| v1, all 41 pairs | 83% | 0.31 | 7 lenient, 0 strict |
| v1, held-out incidents 11-20 | 84% | **0.00** | lenient only |
| v2, held-out incidents 11-20 (written from 1-10 only) | 90% | **0.60** | 1 lenient, 1 strict |

v1 called almost everything "supported", including how-to sections and passages pointing to the
opposite conclusion; its 96% citation support on the baseline was really ~78% by hand grades.

**CI:** the 10-incident set replays recorded model replies through the real stack on every PR
(`make eval-ci`); it fails on a failed run, more tool errors, or scores below the recording.

## A8 · Packaging and Kubernetes

Local Kubernetes (kind 1.37 + Calico) on an M3 Pro, Docker VM 8 GB / 12 CPUs. Cloud path written
but not applied (by choice: $0), so its cost is an estimate from list prices.

| Metric | Value | How measured |
| --- | --- | --- |
| From empty Docker to all pods ready | **1 min 45 s** | `make kind-up && make kind-install` (cluster, Calico, 6 image builds, load, helm --wait) |
| Restarts on a fresh install | **0** (was 2-4 per service) | init containers wait for their own DB role and the broker |
| Image builds, all six | 13 s | after removing the 2.8 GB trace from the build context |
| Ingest throughput into the cluster | 41,600 events/s | 138,048 events replayed through the NodePort |
| Injected faults detected in the cluster | 3/3 | `scripts/k8s_smoke.sh` |
| Full flow in a browser against the cluster | passed | investigate (pod to Ollama on the host), approve, audit, follow-up, viewer without buttons |
| NetworkPolicy enforcement | 3/3 allowed paths open, 5/5 unneeded paths blocked | TCP probes from inside pods |
| Chart and Terraform misconfigurations, HIGH/CRITICAL | **0** (was 2 CRITICAL + 2 HIGH) | Trivy config |
| Rendered manifests valid | 25/25 (kind values), 24/25 + 1 CRD (Azure values) | kubeconform -strict |
| Azure cost, estimated | **≈ $5.30/day** | Azure Retail Prices API, westus2 list prices (docs/INSTALL.md) |
