---
doc_id: rb-009-node-drain
title: Draining a node for maintenance
failure_types: thermal_runaway, ecc_degradation, gpu_off_bus, nvlink_degradation, pcie_degradation, power_fault
components: node, scheduler
version: 1.5
---
# Draining a node for maintenance

## Approval
Draining removes capacity from the fleet. It always needs approval from the on-call engineer,
including when the incident assistant recommends it. Record the incident ID in the drain reason.

## Slurm
1. Drain so running jobs can finish but no new jobs start:
   `scontrol update nodename=<node> state=drain reason="INC-<id> <short reason>"`
2. Check what's still running: `squeue -w <node>`.
3. If the hardware is failing and jobs are already broken, requeue them:
   `scontrol requeue <jobid>` (only with the job owner's or on-call lead's agreement).
4. Wait until `sinfo -n <node>` shows `drained` (not `draining`) before starting work.

## Kubernetes
1. Cordon to stop new pods: `kubectl cordon <node>`.
2. Evict workloads: `kubectl drain <node> --ignore-daemonsets --delete-emptydir-data --timeout=30m`.
3. Training jobs managed by an operator (e.g. Kubeflow, Volcano) may need to be restarted from
   their last checkpoint after eviction; notify the job owners.

## Returning to service
- Only after the fix is verified (rb-018-burn-in-after-repair).
- Slurm: `scontrol update nodename=<node> state=resume`. Kubernetes: `kubectl uncordon <node>`.
- Close the loop in the incident: what was replaced, burn-in result, time back in service.

## Timing
Prefer draining over killing jobs. For slowly degrading faults (rising ECC rate, PCIe replays),
draining at the next job boundary is fine. For active failures (XID 79, DBE, thermal slowdown),
drain immediately.
