---
doc_id: rb-002-ecc-degradation
title: ECC error degradation (correctable to uncorrectable)
failure_types: ecc_degradation
components: gpu, hbm
version: 1.3
---
# ECC error degradation

## Symptoms
- Correctable (single-bit, SBE) ECC errors on one GPU rise from near zero to dozens per hour.
  Healthy HBM sees roughly one correctable error per GPU per day or less.
- Retired pages (Volta and older) or remapped rows (Ampere and Hopper) increase.
- XID 92 (high single-bit ECC error rate) may appear.
- The failure often ends in an uncorrectable (double-bit, DBE) error with XID 48, which kills the
  running job. On Ampere and Hopper, XID 94 (contained) or XID 95 (uncontained) can follow.

## Why it matters
An accelerating correctable-error rate is the best early warning of an uncorrectable error. Acting
on the trend lets you move work off the GPU before a job crashes.

## Diagnosis
1. Check counters: `nvidia-smi -q -d ECC` (volatile and aggregate SBE/DBE counts).
2. On A100/H100, check row remapping:
   `nvidia-smi --query-remapped-rows=gpu_bus_id,remapped_rows.correctable,remapped_rows.uncorrectable,remapped_rows.pending,remapped_rows.failure --format=csv`.
   See rb-021-hbm-row-remapping for how to read the result.
3. Look at the rate, not the total. Counters are cumulative and reset on driver reload, so compare
   increases over time windows.
4. Search past incidents for the same GPU serial. Repeated ECC incidents on one GPU are an RMA signal.

## Remediation
- Rising SBE rate, no DBE yet: drain the node at the next safe point (rb-009-node-drain), reset the
  GPU so pending row remaps take effect (rb-010-gpu-reset), then run DCGM diagnostics level 3
  (rb-012-dcgm-diagnostics). If errors resume after the reset, RMA the GPU.
- DBE / XID 48 occurred: the job on that GPU is lost. Drain, reset, and check for a remapping
  failure. A remapping failure, or a second DBE within 30 days, means RMA (rb-011-rma-process).

## Escalation
- Sev3 while only correctable errors are rising; sev2 once the rate exceeds 50 per 30 minutes;
  sev1 on DBE or XID 95.
