---
doc_id: rb-018-burn-in-after-repair
title: Burn-in before returning a node to service
failure_types: thermal_runaway, ecc_degradation, gpu_off_bus, nvlink_degradation, pcie_degradation, power_fault
components: node, gpu
version: 1.1
---
# Burn-in before returning a node to service

## Required checks
1. `nvidia-smi` lists every GPU at the expected PCIe width and speed (rb-006-pcie-degradation).
2. `dcgmi diag -r 3` passes on all GPUs (rb-012-dcgm-diagnostics).
3. A 30-minute full-load run (DCGM targeted stress or the team's standard training benchmark) with:
   - no XIDs,
   - no new ECC errors, PCIe replays, or NVLink CRC errors,
   - temperatures in the normal range for the model (H100 below about 75 C, A100 below about 72 C
     at full load with normal inlet),
   - power reaching the default limit, not capped.
4. All fans and PSUs report healthy in the BMC.

## Recording
Record the results in the incident before resuming the node (rb-009-node-drain). A node that
fails burn-in stays drained and goes back to diagnosis.
