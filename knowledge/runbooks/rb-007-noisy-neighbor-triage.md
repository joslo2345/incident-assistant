---
doc_id: rb-007-noisy-neighbor-triage
title: Workload change or hardware fault? (noisy neighbor triage)
failure_types: noisy_neighbor
components: gpu, scheduler
version: 1.0
---
# Workload change or hardware fault? (noisy neighbor triage)

## When to use this
A GPU or group of GPUs suddenly shows high utilization, power, and temperature, but there are no
hardware errors: no XIDs, no ECC or PCIe/NVLink counter increases, no BMC warnings. This is
usually a workload change, such as a new job, a noisy neighbor sharing the node, or a job moving
to these GPUs, not a fault.

## How to tell the difference
- Check temperature against power. A healthy GPU's temperature follows its power with a lag of
  about 90 seconds. If temperature is high but consistent with the power draw, the GPU is simply
  busy. If temperature is more than about 5 C above what the power explains, suspect cooling
  (rb-001-thermal-runaway).
- Check the scheduler. A job start on those GPUs at the same time as the change confirms a
  workload shift (`squeue -w <node>` in Slurm, or the pods on the node in Kubernetes).
- Look at error counters. No change in ECC, PCIe replay, or NVLink counters means no hardware
  evidence.
- Look at throttle reasons. `sw_power_cap` at full load is normal for H100 and A100; thermal
  slowdown is not.

## What to do
- Workload shift confirmed: no hardware action. If the neighbor is degrading another tenant's job,
  that's a scheduling issue: check GPU isolation (MIG or exclusive mode) and raise it with the
  platform team.
- Uncertain: keep watching for 30 minutes. A real fault produces error signals or a growing thermal
  residual; a workload shift stays explained by power.
- Never drain a node just because utilization is high.

## Escalation
- Informational (sev4). No page.
