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
