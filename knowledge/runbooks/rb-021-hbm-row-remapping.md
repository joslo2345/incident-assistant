---
doc_id: rb-021-hbm-row-remapping
title: HBM row remapping (A100 and H100)
failure_types: ecc_degradation
components: gpu, hbm
version: 1.0
source: https://docs.nvidia.com/deploy/a100-gpu-mem-error-mgmt/
---
# HBM row remapping (A100 and H100)

## Background
Ampere and Hopper GPUs replace faulty HBM rows with spare rows ("row remapping") instead of
retiring whole memory pages, which older GPUs did. In our telemetry, `retired_pages_total` carries
the remapped-row count for these GPUs.

## Reading the state
`nvidia-smi --query-remapped-rows=gpu_bus_id,remapped_rows.correctable,remapped_rows.uncorrectable,remapped_rows.pending,remapped_rows.failure --format=csv`

- `correctable` / `uncorrectable`: rows remapped because of correctable or uncorrectable errors.
- `pending` = yes: a remap is waiting for a GPU reset to take effect (rb-010-gpu-reset). Until then
  the bad row is still in use.
- `failure` = yes: the GPU ran out of spare rows or couldn't record the remap. RMA the GPU
  (rb-011-rma-process).

## Decision table
- A few correctable remaps, no pending, no failure: normal ageing. No action.
- Pending remap: reset the GPU at the next drain.
- Uncorrectable remap after XID 48: reset, then run `dcgmi diag -r 3`; RMA on any failure.
- Remap failure or XID 64: RMA.
