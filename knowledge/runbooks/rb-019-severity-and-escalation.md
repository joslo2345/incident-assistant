---
doc_id: rb-019-severity-and-escalation
title: Incident severity and escalation
failure_types: unknown
components: process
version: 2.1
---
# Incident severity and escalation

## Severity levels
- Sev1: a GPU or node is down or jobs are failing (XID 79, uncorrectable ECC errors, node
  unreachable), or several nodes are affected at once. Page the on-call engineer immediately;
  acknowledge within 15 minutes.
- Sev2: degraded hardware with production impact or an imminent failure (thermal throttling,
  power capping after PSU loss, rapidly rising ECC rate, NVLink degradation slowing training).
  Respond within 1 hour during business hours, 4 hours otherwise.
- Sev3: an anomaly worth fixing at the next maintenance window (slowly rising correctable errors,
  PCIe replays without errors). Handle in the next working day.
- Sev4: informational (noisy neighbors, single inlet warnings). No action required.

## Escalation paths
- Facility problems (rack-wide temperature, PDU failures): datacenter operations on-call.
- Repeated failures on one GPU: hardware vendor RMA (rb-011-rma-process).
- Driver or firmware bugs: platform team, with a bug report attached.
- Customer-facing capacity loss over 5% of the fleet: incident commander and account team.

## Approval for actions
Actions that change fleet state (draining a node, resetting GPUs, rebooting) always need approval
from the on-call engineer, even when the incident assistant recommends them.
