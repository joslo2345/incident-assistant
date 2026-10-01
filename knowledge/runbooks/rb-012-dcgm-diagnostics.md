---
doc_id: rb-012-dcgm-diagnostics
title: Running DCGM diagnostics
failure_types: ecc_degradation, gpu_off_bus, nvlink_degradation, pcie_degradation, thermal_runaway
components: gpu, nvlink, pcie
version: 1.0
source: https://docs.nvidia.com/datacenter/dcgm/latest/user-guide/dcgm-diagnostics.html
---
# Running DCGM diagnostics

## Levels
- `dcgmi diag -r 1`: quick checks (deployment, software, basic health). Seconds. Safe to run on a
  node with jobs.
- `dcgmi diag -r 2`: adds short functional tests, including PCIe and NVLink bandwidth. A few
  minutes. Run on a drained node.
- `dcgmi diag -r 3`: adds longer stress and memory tests. Tens of minutes. The standard check
  after a repair or before an RMA.
- `dcgmi diag -r 4`: extended tests. Can take hours; use when level 3 passes but the fault keeps
  coming back.

## Rules
- Levels 2 and above need an idle GPU: drain first (rb-009-node-drain).
- Run against specific GPUs with `-i <index list>` to save time when only some are suspect.
- Save the JSON output (`-j`) and attach it to the incident and any RMA (rb-011-rma-process).

## Reading results
- A failed memory test after ECC errors confirms degraded HBM.
- Low PCIe bandwidth on one GPU matches a downtrained link (rb-006-pcie-degradation).
- Failed NVLink tests on a GPU pair match rb-005-nvlink-degradation.
- A thermal or power test failing points at cooling (rb-001) or power delivery (rb-003).
