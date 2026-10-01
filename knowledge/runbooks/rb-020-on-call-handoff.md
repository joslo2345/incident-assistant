---
doc_id: rb-020-on-call-handoff
title: On-call handoff and incident communication
failure_types: unknown
components: process
version: 1.0
---
# On-call handoff and incident communication

## Updates during an incident
- Sev1: post an update in the incident channel at least every 30 minutes, even if nothing changed.
- Each update says: current impact, what's been done, what's next, and when the next update comes.

## Handoff
At shift change, the outgoing engineer hands over every open sev1-sev3 incident with:
1. Current status and the latest evidence (link the incident record).
2. Actions taken and their results, including drains and resets.
3. Pending approvals and who is waiting on whom (vendor RMA, datacenter operations).
4. The next decision point and its deadline.

## Closing an incident
Close only after the node is back in service or formally retired, with root cause, fix, and time
to resolve recorded. These records become the past-incident knowledge the assistant searches,
so be specific: name the part replaced and the evidence that confirmed the cause.
