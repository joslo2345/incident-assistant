---
doc_id: rb-006-pcie-degradation
title: PCIe link degradation and replays
failure_types: pcie_degradation
components: pcie, gpu, node
version: 1.0
---
# PCIe link degradation and replays

## Symptoms
- The GPU's PCIe replay counter increases steadily (tens to hundreds per minute). Healthy links
  stay at or near zero.
- Host-to-device transfer bandwidth drops; data loading becomes a bottleneck and GPU utilization
  falls slightly (around 10%).
- Correctable AER errors in the kernel log for the GPU's PCIe path.
- The link may have trained at a lower width or speed than it supports.

## Diagnosis
1. Check the replay counter: `nvidia-smi -q` (PCI section, "Replays Since Reset"), or DCGM field
   `DCGM_FI_DEV_PCIE_REPLAY_COUNTER`.
2. Check the trained link: `lspci -vvv -s <bus-id> | grep -E 'LnkCap|LnkSta'`. LnkSta should match
   LnkCap (for example x16 at 32 GT/s for PCIe Gen5).
3. Look for AER messages: `journalctl -k | grep -i aer`.
4. Check whether other GPUs behind the same PCIe switch show replays. Several GPUs on one switch
   point at the switch or retimer, not the GPU.

## Remediation
- Replays without errors are not urgent, but they often come before a GPU falls off the bus
  (rb-004-gpu-off-bus). Schedule a drain.
- Reseat the GPU and riser or cable during the maintenance window, then confirm the link trains at
  full width and speed and the counter stays flat under load (rb-018-burn-in-after-repair).
- If replays persist after reseating, replace the riser or cable, then the GPU (rb-011-rma-process).

## Escalation
- Sev3; raise to sev2 if the link downtrains or AER uncorrectable errors appear.
