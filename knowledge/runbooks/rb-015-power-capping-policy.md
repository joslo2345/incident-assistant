---
doc_id: rb-015-power-capping-policy
title: Power capping policy
failure_types: power_fault
components: gpu, psu, node
version: 1.0
---
# Power capping policy

## Defaults
- H100 SXM: 700 W default limit. A100 SXM: 400 W. We run at default limits in normal operation.
- `sw_power_cap` throttling at full load is expected and is not a fault.

## When nodes get capped
- Automatically by the BMC or node manager when PSU redundancy is lost (rb-003-power-fault).
- Manually during facility power or cooling events, by the on-call lead.

## Checking and changing limits
- Current limits: `nvidia-smi -q -d POWER` (Enforced Power Limit vs Default Power Limit), or
  `nvidia-smi --query-gpu=power.limit,power.default_limit --format=csv`.
- Set a temporary limit (needs root, resets on reboot unless persisted): `nvidia-smi -pl <watts> -i <gpu>`.
- Restore the default: `nvidia-smi -pl <default watts> -i <gpu>`, or reboot.

## Rules
- Record every manual cap in the incident with a reason and an end condition.
- Remove caps as soon as the condition ends; a forgotten cap looks like a power fault later.
- If an enforced limit stays low after PSU redundancy is restored, check the BMC power policy
  (Redfish `PowerSubsystem` / vendor node-manager settings) before blaming the GPUs.
