# Results

Measured numbers, one section per package. Each row says how it was measured and on what.

## A0 · Foundation

| Metric | Value | How measured |
| --- | --- | --- |
| Ingest image size | 259 MB | `docker image ls`, python:3.12-slim base, arm64 (MacBook) |
| Test suite | 40 tests, 0.3 s | `make test` locally |
