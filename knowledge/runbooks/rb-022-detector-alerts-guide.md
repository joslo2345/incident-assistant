---
doc_id: rb-022-detector-alerts-guide
title: What the detector's alerts mean
failure_types: thermal_runaway, ecc_degradation, power_fault, gpu_off_bus, nvlink_degradation, pcie_degradation, noisy_neighbor
components: detector, process
version: 1.0
---
# What the detector's alerts mean

The incident assistant's detector groups alerts into incidents. Each piece of evidence starts with
the alert kind in brackets. This page explains them.

## Event alerts
- `[xid_79]`, `[pcie_fatal]`, `[gpu_missing]`: the GPU fell off the bus or stopped reporting.
  See rb-004-gpu-off-bus.
- `[xid_48]`, `[ecc_dbe]`: uncorrectable ECC error. See rb-002-ecc-degradation.
- `[xid_74]`: NVLink error. See rb-005-nvlink-degradation.
- `[fan_failure]`: BMC fan critical (often 0 RPM). See rb-001-thermal-runaway.
- `[psu_fault]`: BMC power supply error. See rb-003-power-fault.
- `[inlet_warning]`: inlet temperature warning; attached as context only. See rb-014.

## Threshold alerts
- `[thermal_throttle]`: thermal slowdown in the throttle reasons. `[temp_high]`: at or above 83 C.
- `[power_limit_reduced]`: enforced power limit below 90% of the GPU's nominal limit.
- `[ecc_sbe]`: 5 or more correctable errors in 30 minutes. `[ecc_sbe_burst]`: 50 or more.
- `[retired_pages]`: new retired pages or remapped rows.
- `[pcie_replays]`: 100 or more PCIe replays in 10 minutes. `[nvlink_errors]`: 50 or more NVLink
  CRC errors in 10 minutes.

## Model alerts
- `[thermal_residual]`: the GPU is hotter than its power draw predicts, by 5 C or more for 3
  minutes. This is the earliest sign of a cooling problem, often before any throttling.
- `[workload_shift]`: a big change in temperature, power, or utilization that the power draw
  explains. Usually a new job or noisy neighbor (rb-007-noisy-neighbor-triage). Informational.

## Reading an incident
The suspected failure type comes from the strongest evidence, in order: GPU off bus, ECC, power,
thermal, NVLink, PCIe, then workload. Check the evidence yourself before approving any action.
