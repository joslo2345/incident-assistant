---
doc_id: rb-014-inlet-temperature
title: Inlet temperature warnings and facility cooling
failure_types: thermal_runaway, unknown
components: node, facility
version: 1.0
---
# Inlet temperature warnings and facility cooling

## Symptoms
- BMC warnings for the inlet (front-panel) temperature sensor crossing a warning threshold,
  typically around 35 C, usually clearing within minutes.
- Occasionally several nodes in the same rack or row warn at once.

## What it means
A single short inlet warning on one node is common and usually harmless: a door left open, a
brief CRAC cycle, a hot spot. It becomes important when:
- several nodes in a rack or row warn together (facility cooling problem), or
- warnings on one node become frequent or long (local airflow obstruction), or
- GPU temperatures on the node rise together with the inlet.

## What to do
1. Check whether neighbouring nodes in the rack show the same warning.
2. Rack-wide: page datacenter operations; consider pre-emptive capping (rb-015-power-capping-policy)
   to reduce heat load until cooling is restored.
3. Single node, frequent: inspect for blocked intakes, missing blanking panels, or cable bundles in
   front of the node at the next maintenance window.
4. If GPU temperatures are also high and a fan is degraded, follow rb-001-thermal-runaway.

## Escalation
- Single short warning: informational (sev4), no action.
- Rack-wide or with rising GPU temperatures: sev2.
