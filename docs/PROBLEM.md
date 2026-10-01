# Problem statement

## Who the customer is

An organization running its own GPU cluster for training and inference: a few hundred to a few
thousand GPUs in 8-GPU servers, an infrastructure team of a handful of engineers, and on-site
or remote technicians who replace parts.

## The pain

When a GPU or server fails, working out *why* is slow and manual:

- The signals are scattered: GPU metrics, driver XID errors, BMC logs and job schedulers each live
  in a different tool.
- Many failures look alike from the outside. A thermal problem caused by a dead fan, a degrading
  HBM stack and a misbehaving job can all show up as "GPU slow, then job crashed."
- The fix depends on the cause: drain and replace a fan, reset the GPU, replace the GPU, or do
  nothing because the workload was the problem. Guessing wrong wastes technician time and expensive GPU hours.
- What the team has learned is spread across runbooks, vendor docs and old tickets, and
  depends on whoever is on call.

Today, a diagnosis typically takes an engineer **hours**, while the failed GPU or node sits idle or
keeps crashing jobs.

## What we build

A system that watches fleet telemetry, opens an incident when hardware looks unhealthy, and has an
AI agent investigate it like an experienced engineer would: check metrics and logs, look up the
runbook and similar past incidents, and return a **diagnosis with cited evidence and a recommended
action**. Anything that changes the fleet, such as draining a node, needs human approval.

## How success is measured

| Metric | Target |
| --- | --- |
| Time to diagnosis (incident opened → diagnosis ready) | under 2 minutes, compared with hours manually |
| Root-cause accuracy on labeled faults | ≥ 85% |
| Citation correctness (cited source supports the claim) | ≥ 90% |
| Unsafe action rate (state change without approval) | 0 |
| Detection: recall / time to detect per failure type | reported per type; noisy-neighbor false positives analyzed |
| Cost per investigated incident | reported, and compared between hosted and self-hosted models |

The targets are starting assumptions and get revised once A3 and A6 produce real numbers.

## Out of scope

- Fixing anything automatically: the system recommends and asks; people decide.
- Job scheduling or capacity planning.
- Non-GPU infrastructure (storage, network fabric) beyond what shows up in node telemetry.
