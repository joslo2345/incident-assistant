# Detector evaluation (A3)

Three 48 h replays through the full stack (ingest → Redpanda → TimescaleDB), each with 28 injected
faults: 4 of each of the 6 failure types plus 4 noisy-neighbor decoys. Half the faults have their
BMC log entries dropped, because in practice BMC logs often don't make it into the pipeline. Seeds
11 (ev2) and 23 (ev3) were used while developing; **seed 37 (ev4) was held out** and only run after
the last detector change.

Reproduce: `make up && uv run python scripts/eval_detection.py --run-id ev4 --duration 2d --seed 37`

## Pooled results (72 faults, 12 decoys)

| Config | Recall | Alarms | Precision | Decoys that raised an alarm | Correct type |
| --- | --- | --- | --- | --- | --- |
| v1 static thresholds + event rules | 97% (70/72) | 72 | 100% | 0/12 | 70/70 |
| v2 + per-GPU rolling z-scores | 99% (71/72) | 381 | **21%** | 8/12 | 69/71 |
| **v3 + thermal residual model** | **99% (71/72)** | 73 | **100%** | **0/12** | **71/71** |

## Per failure type (v3)

| Failure type | Faults | Recall | Time to detect, median | max |
| --- | --- | --- | --- | --- |
| gpu_off_bus | 12 | 100% | 0.1 min | 0.1 min |
| power_fault | 12 | 100% | 0.4 min | 0.8 min |
| pcie_degradation | 12 | 100% | 1.2 min | 1.8 min |
| nvlink_degradation | 12 | 100% | 3.9 min | 4.8 min |
| thermal_runaway | 12 | 92% | 4.9 min | 7.1 min |
| ecc_degradation | 12 | 100% | 76.7 min | 122.8 min |

ECC degradation takes longest because it ramps over hours; it's still caught a median of about 1 h
*before* the uncorrectable error. Detection is far earlier than the DBE, which is the point.

## What each layer contributes

- **Static thresholds and event rules** already catch most faults, because XIDs, error counters and
  enforced power limits are explicit signals. Without BMC logs, they miss thermal faults on GPUs
  that aren't busy enough to throttle.
- **Naive z-scores** add one detection and **309 false alarms**. On a bursty ML cluster, every job
  start or stop is a multi-sigma jump in power, utilization and temperature. They also flagged 8 of
  12 noisy-neighbor decoys as faults.
- **The thermal residual model** (temperature predicted from lag-filtered power, a per-GPU
  intercept, and a fleet slope per GPU model) keeps the extra detection: a fan failure on
  partly loaded GPUs, found in 4.7 min with no BMC log and no throttling. It downgrades every
  z-spike that power explains to `workload_shift`, which removes all false alarms. Decoys are labelled
  `noisy_neighbor` (informational) or ignored.

## The one miss

A fan failure on **idle** GPUs (55 W) whose BMC entry was dropped. At idle power the dead fan adds
about 4 °C, which is within normal variation. No telemetry-only method can see it, and the fix
belongs in the data (collect BMC fan RPM as a metric), not in the detector.

## Bugs the evaluation found (fixed)

1. **Negative time-to-detect.** Metric alerts were stamped at the start of their minute (the data
   exists only at its end), and fault events could predate the fault by one sample.
2. **Unrelated faults merged.** Grouping only by node folded a PCIe fault into an NVLink incident
   that ended 25 min earlier on other GPUs. Alerts now join an incident only if they're node-level,
   share a GPU with it, or point at the same failure type.
3. **A gap between "explained" and "overheating"** in the residual layer let z-spikes with a 3–5 °C
   model error through as actionable alerts. Now any z-spike that isn't overheating counts as workload.

## Limits

The faults are simulated, so their signatures are cleaner than real hardware's, and these numbers are
an upper bound for the static layer. The detector's relative behavior (z-scores flood a bursty cluster,
physics-aware baselines fix it) is the transferable result.

## Held-out run (ev4, seed 37), full report


48 h replayed (1,104,952 events), 28 injected faults, seed 37, BMC dropout 50%. Window 2026-09-29T08:08:05+00:00 to 2026-10-01T08:08:05+00:00.

| Config | Recall | Precision | False alarms | Decoy alarms | TTD median | Classified |
| --- | --- | --- | --- | --- | --- | --- |
| v1-static (Static thresholds + event rules) | 100% | 100% | 0 | 0/4 | 0.8 min | 24/24 |
| v2-zscore (+ per-GPU rolling z-scores) | 100% | 20% | 99 | 3/4 | 0.8 min | 24/24 |
| v3-residual (+ thermal residual model (temp vs power)) | 100% | 100% | 0 | 0/4 | 0.8 min | 24/24 |

### v1-static: Static thresholds + event rules

| Failure type | Faults | Recall | Classified | TTD median | TTD max |
| --- | --- | --- | --- | --- | --- |
| ecc_degradation | 4 | 100% | 4/4 | 66.9 min | 89.3 min |
| gpu_off_bus | 4 | 100% | 4/4 | 0.1 min | 0.1 min |
| nvlink_degradation | 4 | 100% | 4/4 | 4.1 min | 4.8 min |
| pcie_degradation | 4 | 100% | 4/4 | 1.2 min | 1.4 min |
| power_fault | 4 | 100% | 4/4 | 0.2 min | 0.6 min |
| thermal_runaway | 4 | 100% | 4/4 | 0.1 min | 5.3 min |
| **all faults** | 24 | 100% | 24/24 | 0.8 min | 89.3 min |

Precision 100% (24/24 alarms match a real fault); false alarms by type: none.
Noisy-neighbor decoys: 4; raised an alarm 0, labelled noisy_neighbor 0, ignored 4.

### v2-zscore: + per-GPU rolling z-scores

| Failure type | Faults | Recall | Classified | TTD median | TTD max |
| --- | --- | --- | --- | --- | --- |
| ecc_degradation | 4 | 100% | 4/4 | 66.9 min | 89.3 min |
| gpu_off_bus | 4 | 100% | 4/4 | 0.1 min | 0.1 min |
| nvlink_degradation | 4 | 100% | 4/4 | 3.9 min | 4.8 min |
| pcie_degradation | 4 | 100% | 4/4 | 1.2 min | 1.4 min |
| power_fault | 4 | 100% | 4/4 | 0.2 min | 0.6 min |
| thermal_runaway | 4 | 100% | 4/4 | 0.1 min | 5.3 min |
| **all faults** | 24 | 100% | 24/24 | 0.8 min | 89.3 min |

Precision 20% (25/124 alarms match a real fault); false alarms by type: {'thermal_runaway': 43, 'unknown': 56}.
Noisy-neighbor decoys: 4; raised an alarm 3, labelled noisy_neighbor 0, ignored 1.

### v3-residual: + thermal residual model (temp vs power)

| Failure type | Faults | Recall | Classified | TTD median | TTD max |
| --- | --- | --- | --- | --- | --- |
| ecc_degradation | 4 | 100% | 4/4 | 66.9 min | 89.3 min |
| gpu_off_bus | 4 | 100% | 4/4 | 0.1 min | 0.1 min |
| nvlink_degradation | 4 | 100% | 4/4 | 4.1 min | 4.8 min |
| pcie_degradation | 4 | 100% | 4/4 | 1.2 min | 1.4 min |
| power_fault | 4 | 100% | 4/4 | 0.2 min | 0.6 min |
| thermal_runaway | 4 | 100% | 4/4 | 0.1 min | 5.3 min |
| **all faults** | 24 | 100% | 24/24 | 0.8 min | 89.3 min |

Precision 100% (24/24 alarms match a real fault); false alarms by type: none.
Noisy-neighbor decoys: 4; raised an alarm 0, labelled noisy_neighbor 3, ignored 1.
