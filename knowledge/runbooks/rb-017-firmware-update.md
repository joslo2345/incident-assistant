---
doc_id: rb-017-firmware-update
title: GPU and BMC firmware updates
failure_types: unknown
components: gpu, bmc, firmware
version: 1.0
---
# GPU and BMC firmware updates

## When to update
- Recurring GSP errors (XID 119/120) or micro-controller halts (XID 62) that a vendor advisory
  ties to firmware.
- BMC bugs: wrong sensor readings, missing log entries, Redfish endpoints failing.
- Fleet-wide planned updates, rolled out in waves.

## Rules
- Never update a whole cluster at once. Update one node, run burn-in (rb-018-burn-in-after-repair),
  then a small canary group for at least 24 hours, then the rest in waves.
- Keep driver, GPU VBIOS, NVSwitch, and BMC versions within the vendor's support matrix.
- Drain before updating (rb-009-node-drain). Many updates need a cold power cycle, not just a reboot.
- Record the before and after versions in the node inventory.

## Checking versions
- Driver and VBIOS: `nvidia-smi -q | grep -iE 'driver version|vbios'`.
- BMC: Redfish `GET /redfish/v1/Managers/<id>` (`FirmwareVersion`).
