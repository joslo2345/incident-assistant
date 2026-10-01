---
doc_id: rb-016-driver-reload
title: Reloading the NVIDIA driver
failure_types: unknown
components: driver, gpu
version: 1.0
---
# Reloading the NVIDIA driver

## When
- A GPU reset fails but the node doesn't need a full reboot.
- After a driver upgrade without reboot (prefer a reboot if possible).

Reloading clears volatile error counters. Record ECC and replay counters before reloading so the
history isn't lost.

## Procedure
1. Drain the node (rb-009-node-drain) and stop all GPU processes.
2. Stop services that hold the GPUs: `systemctl stop nvidia-persistenced`, the DCGM host engine,
   and on NVSwitch systems `nvidia-fabricmanager`.
3. Unload modules: `rmmod nvidia_uvm nvidia_drm nvidia_modeset nvidia`.
4. Load: `modprobe nvidia`, then start the services again (fabric manager first on NVSwitch systems).
5. Check `nvidia-smi` lists every GPU, then run `dcgmi diag -r 1`.

If a module won't unload ("in use"), something still holds the device: check `lsof /dev/nvidia*`.
If it still fails, reboot.
