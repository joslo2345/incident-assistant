---
doc_id: rb-004-gpu-off-bus
title: GPU fell off the bus (XID 79)
failure_types: gpu_off_bus
components: gpu, pcie, node
version: 1.4
---
# GPU fell off the bus (XID 79)

## Symptoms
- Kernel log: `NVRM: Xid (PCI:0000:xx:00): 79, GPU has fallen off the bus.`
- The GPU disappears from telemetry: no samples, `nvidia-smi` reports "Unable to determine the
  device handle" or hangs.
- Often a BMC critical-interrupt entry for a PCIe fatal error on the same slot.
- Every job using that GPU fails. Multi-GPU jobs on the node usually fail too.

## Likely causes
- PCIe link failure (riser, retimer, cable, or slot problem).
- GPU hardware fault or GPU power delivery problem.
- Severe overheating that tripped GPU protection (check thermal history first).
- Less often a driver bug; look for a known issue for the driver version in use.

## Diagnosis
1. Confirm the XID in `dmesg` or the system journal, and note the PCI bus ID.
2. Check whether the device is still enumerated: `lspci | grep -i nvidia`. A missing device or one
   with `rev ff` means the link is down.
3. Look for PCIe AER errors before the XID (`journalctl -k | grep -i aer`). A history of
   correctable PCIe replays on that GPU points at the link (see rb-006-pcie-degradation).
4. Check the GPU's temperature and power history in the minutes before the event.

## Remediation
1. Drain the node (rb-009-node-drain). The GPU can't be recovered while the OS is running.
2. Reboot the node; a GPU reset is not enough after XID 79.
3. If the GPU returns, run DCGM diagnostics level 3 (rb-012-dcgm-diagnostics) before returning the
   node to service.
4. A second XID 79 on the same GPU within 30 days: reseat the GPU or riser; if it happens again,
   RMA (rb-011-rma-process).

## Escalation
- Sev1: capacity loss and failed jobs.
