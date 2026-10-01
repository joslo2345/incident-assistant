---
doc_id: rb-008-xid-reference
title: XID error reference for operations
failure_types: gpu_off_bus, ecc_degradation, nvlink_degradation, unknown
components: gpu, driver
version: 2.0
source: https://docs.nvidia.com/deploy/xid-errors/
---
# XID error reference for operations

XID errors are reported by the NVIDIA driver in the kernel log as `NVRM: Xid (PCI:<bus id>): <code>,
<message>`. This page summarizes the codes we see most and what operations should do. NVIDIA's XID
documentation (linked in the metadata) is the authoritative list.

## Application-caused (usually not hardware)
- XID 13: graphics engine exception. Usually an application bug (illegal memory access, invalid
  instruction). Check whether the same job fails on other GPUs before suspecting hardware.
- XID 31: GPU memory page fault (MMU fault). Usually the application; repeated XID 31 on one GPU
  across different jobs points at hardware.
- XID 43: GPU stopped processing. Often follows an application fault; not actionable alone.
- XID 45: preemptive cleanup after another error. Look at the XID that came before it.

## Memory (ECC)
- XID 48: uncorrectable (double-bit) ECC error. The job on that GPU is lost. Follow
  rb-002-ecc-degradation.
- XID 63: ECC page retirement or row remapping recorded. Expected after ECC errors; a GPU reset
  applies pending remaps (rb-021-hbm-row-remapping).
- XID 64: page retirement or row remapping failed to record. RMA candidate.
- XID 92: high single-bit ECC error rate. Early warning; see rb-002-ecc-degradation.
- XID 94: contained ECC error (Ampere and later). The affected application is terminated; other
  work on the GPU can continue. Reset the GPU when it's idle.
- XID 95: uncontained ECC error. Reset the GPU; reboot the node if the reset fails.

## Interconnect and bus
- XID 74: NVLink error. See rb-005-nvlink-degradation.
- XID 79: GPU has fallen off the bus. See rb-004-gpu-off-bus. Requires a node reboot.

## Firmware and driver
- XID 61 and 62: internal micro-controller warning / halt. Collect a bug report
  (`nvidia-bug-report.sh`) and reset the GPU; recurring 62 is a hardware or firmware problem.
- XID 119 and 120: GSP (GPU System Processor) RPC timeout / error. Reset the GPU; recurring errors
  usually need a driver or firmware update (rb-017-firmware-update).

## General rule
Map the XID to the GPU by PCI bus ID, not by index; indexes can change between boots. Always look
at the first XID in a burst, since later ones are often consequences.
