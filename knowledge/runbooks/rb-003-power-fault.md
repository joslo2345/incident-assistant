---
doc_id: rb-003-power-fault
title: PSU failure and node power capping
failure_types: power_fault
components: psu, node, gpu
version: 1.1
---
# PSU failure and node power capping

## Symptoms
- BMC event `ResourceErrorsDetected` for a power supply, often with error type "Input lost"
  (Redfish `ResourceEvent` registry), or a PSU sensor SEL entry.
- Every GPU on the node reports a lower enforced power limit at the same time, for example 420 W
  instead of 700 W on H100 SXM.
- GPUs at load show `sw_power_cap` throttling and lower clocks; jobs slow down by 10-40%.
- Temperatures often drop, because the GPUs are drawing less power.

## Likely causes
- One PSU lost input power (tripped PDU breaker, unplugged cord, failed PDU outlet).
- Failed PSU module.
- Node manager or BMC power capping policy triggered after a redundancy loss. With N+N redundancy
  lost, the BMC caps the node so the remaining supplies aren't overloaded.

## Diagnosis
1. Check PSU status in Redfish (`/redfish/v1/Chassis/<id>/PowerSubsystem/PowerSupplies`) or with
   `ipmitool sdr type "Power Supply"`.
2. Confirm the cap on the GPUs: `nvidia-smi -q -d POWER` shows Enforced Power Limit versus Default
   Power Limit.
3. Check the PDU for the affected outlet. If several nodes on one PDU lost a PSU, it's a PDU or
   circuit problem: escalate to datacenter operations.

## Remediation
- Restore input power or replace the PSU module (usually hot-swappable).
- After redundancy is restored, confirm the enforced power limit returns to default. If it doesn't,
  clear the BMC power policy (see rb-015-power-capping-policy).
- Draining isn't required for a PSU swap on redundant chassis, but expect degraded performance
  until the cap is lifted. Drain if the node is running latency-sensitive inference.

## Escalation
- Sev2: performance is degraded and redundancy is gone, so one more PSU failure takes the node down.
