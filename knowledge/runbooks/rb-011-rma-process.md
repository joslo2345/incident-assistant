---
doc_id: rb-011-rma-process
title: Hardware replacement and RMA
failure_types: ecc_degradation, gpu_off_bus, nvlink_degradation, pcie_degradation, thermal_runaway, power_fault
components: gpu, node, fan, psu
version: 1.1
---
# Hardware replacement and RMA

## When to RMA a GPU
- Row remapping failure (XID 64), or `remapped_rows.failure` reported.
- A second uncorrectable ECC error (XID 48) within 30 days.
- A second XID 79 after reseating the GPU and riser.
- NVLink errors that return after a baseboard reset and reseat.
- DCGM diagnostics level 3 failing after a reset and reboot.

## Evidence to collect
Vendors reject RMAs without evidence. Attach:
1. `nvidia-bug-report.sh` output collected right after the failure (before a reboot if possible).
2. `dcgmi diag -r 3` results.
3. The GPU serial number and PCI bus ID: `nvidia-smi --query-gpu=serial,pci.bus_id --format=csv`.
4. The XID lines from the kernel log, and ECC/remapping counters (rb-002-ecc-degradation).
5. The incident ID and timeline from the incident assistant.

## Fans and PSUs
Fans and PSUs are field-replaceable and usually hot-swappable. Swap from spares, then return the
failed part to the vendor under warranty. Record the part serial in the incident.

## After replacement
Run burn-in (rb-018-burn-in-after-repair) before returning the node to service, and record the
new GPU serial in the node inventory.
