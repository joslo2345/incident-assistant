---
doc_id: rb-010-gpu-reset
title: Resetting a GPU
failure_types: ecc_degradation, nvlink_degradation
components: gpu, driver
version: 1.2
---
# Resetting a GPU

## When a reset helps
- To apply pending ECC row remaps or page retirements (rb-021-hbm-row-remapping).
- After XID 94/95, XID 61/62, or XID 119/120, once the GPU is idle.
- To retrain degraded NVLinks.

A reset does not help after XID 79 (GPU off the bus); that needs a node reboot.

## Preconditions
- No processes may be using the GPU. Drain the node first (rb-009-node-drain) and check with
  `nvidia-smi --query-compute-apps=pid,process_name --format=csv`.
- Stop GPU monitoring agents that hold the device open (e.g. the DCGM host engine) if the reset
  reports the device is in use.
- On HGX systems with NVSwitch, NVLink-connected GPUs are reset together; resetting a single GPU
  may not be supported. Reset all GPUs, or reboot the node.

## Procedure
1. `nvidia-smi -r -i <gpu index>` (or omit `-i` to reset all GPUs on supported systems).
2. Confirm the GPU is back: `nvidia-smi -q -i <gpu index>` and check that pending remaps cleared.
3. Run `dcgmi diag -r 2` at minimum before returning the node to service.

## If the reset fails
Reboot the node. If the GPU still isn't healthy after a reboot, open an RMA (rb-011-rma-process).
