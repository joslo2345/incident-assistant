---
doc_id: rb-001-thermal-runaway
title: Thermal runaway and fan failure
failure_types: thermal_runaway
components: fan, gpu, node
version: 1.2
---
# Thermal runaway and fan failure

## Symptoms
- BMC SEL entry for a fan sensor crossing a critical low threshold, often with a reading of 0 RPM
  (Redfish message `SensorThresholdCriticalLowGoingLow`).
- GPU temperatures in one cooling zone climb over several minutes while power stays flat or drops.
- `hw_thermal_slowdown` / `sw_thermal_slowdown` throttle reasons appear and SM clocks fall 30-45%.
- Jobs on the affected GPUs slow down; training step time rises on every rank that shares the node.
- A GPU running more than about 5 C hotter than its power draw predicts is the earliest telemetry
  sign, before any throttling.

## Likely causes
- Failed or failing chassis fan (bearing failure, connector, fan controller).
- Blocked airflow: missing blanking panel, cable bundle in front of the intake, clogged filter.
- Hot aisle containment breach or CRAC unit failure (affects many nodes at once; see
  rb-014-inlet-temperature).
- Degraded thermal interface material on a single GPU (one GPU hot, its neighbours normal).

## Diagnosis
1. Read the fan sensors: `ipmitool sdr type Fan`, or Redfish
   `GET /redfish/v1/Chassis/<id>/ThermalSubsystem/Fans`. A fan at 0 RPM or far below its peers
   confirms the fault.
2. Check which GPUs are affected. A cooling zone usually covers four GPUs (0-3 or 4-7 on 8-GPU
   baseboards). If only one GPU is hot, suspect that GPU's heatsink rather than a fan.
3. Compare inlet temperature with neighbouring nodes. If the whole rack is warm, the problem is
   facility cooling, not this node.
4. Confirm throttling with `nvidia-smi -q -d PERFORMANCE` (look at Clocks Event Reasons).

## Remediation
- Drain the node before replacing a fan (rb-009-node-drain). Running at thermal slowdown risks
  job timeouts and long-term GPU wear.
- Replace the failed fan module; most are hot-swappable, but follow the chassis service guide.
- After replacement, confirm all fans report normal RPM and GPU temperatures return to their
  usual range under load, then run the burn-in in rb-018-burn-in-after-repair.
- If temperatures stay high with all fans healthy, open a hardware ticket for heatsink or TIM
  inspection.

## Escalation
- Sev2 when throttling is active on production jobs. Sev1 if several nodes in a rack are affected
  (likely facility cooling): page the datacenter operations on-call.
