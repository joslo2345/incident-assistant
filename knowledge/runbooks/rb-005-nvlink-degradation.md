---
doc_id: rb-005-nvlink-degradation
title: NVLink degradation
failure_types: nvlink_degradation
components: nvlink, gpu, nvswitch
version: 1.1
---
# NVLink degradation

## Symptoms
- NVLink CRC (flit or data) error counters climb on two GPUs that share a link, from a few per
  minute to hundreds.
- XID 74 (NVLink error) in the kernel log.
- Multi-GPU training slows down: all-reduce time rises and GPU utilization drops on the affected
  pair (and often the whole job), because links retry or retrain.
- Severe cases: link goes down and NCCL jobs fail or fall back to a slower path.

## Diagnosis
1. Link status: `nvidia-smi nvlink -s -i <gpu>` (look for inactive links or reduced speed).
2. Error counters: `nvidia-smi nvlink -e -i <gpu>` shows CRC and replay/recovery counts per link.
   Compare the two GPUs at each end of the suspected link.
3. On HGX systems with NVSwitch, check the fabric manager log for link training failures.
4. Run `dcgmi diag -r 2` or higher; the NVLink bandwidth tests show the slow link.

## Remediation
- Drain the node (rb-009-node-drain); degraded links slow every job that uses them.
- Reset the GPUs (rb-010-gpu-reset) or reboot the node to retrain the links. On NVSwitch systems,
  reset the whole baseboard rather than single GPUs.
- If errors return after a reboot, the baseboard, NVSwitch, or a GPU needs service: open an RMA
  with the error counters and XID log attached (rb-011-rma-process).

## Escalation
- Sev2 when training throughput drops; sev3 if only counters are rising with no performance impact.
