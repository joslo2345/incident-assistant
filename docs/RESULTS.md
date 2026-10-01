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
