---
doc_id: rb-013-bmc-redfish-triage
title: Reading BMC logs (Redfish and IPMI SEL)
failure_types: thermal_runaway, power_fault, gpu_off_bus, unknown
components: bmc, fan, psu, node
version: 1.0
---
# Reading BMC logs (Redfish and IPMI SEL)

## Where to look
- Redfish: `GET /redfish/v1/Managers/<id>/LogServices/<service>/Entries` (service names vary by
  vendor: `SEL`, `EventLog`, `Journal`). Entries have `Severity` (OK, Warning, Critical),
  `MessageId`, `Message`, and for sensor events `SensorType` and `EntryCode` (Assert/Deassert).
- IPMI: `ipmitool sel elist` for the system event log, `ipmitool sdr` for current sensor readings.

## Interpreting entries
- An `Assert` followed later by a `Deassert` for the same sensor is a condition that started and
  cleared. The time between them is how long it lasted.
- Fan `SensorThresholdCriticalLowGoingLow` with reading 0: fan failure (rb-001-thermal-runaway).
- `ResourceErrorsDetected` on a PSU: power supply problem (rb-003-power-fault).
- Critical interrupt / PCIe fatal error on a GPU slot: usually paired with XID 79
  (rb-004-gpu-off-bus).
- Inlet temperature warnings that clear within minutes: usually facility airflow, low priority
  unless frequent (rb-014-inlet-temperature).

## Caveats
- BMC logs aren't always collected. Missing BMC entries don't mean there was no hardware event;
  check GPU telemetry and the kernel log too.
- BMC clocks drift. Line up BMC events with GPU telemetry by a few minutes either way.
- Vendor message IDs (for example Dell `PSU0003`, `FAN0001`) carry the same meaning as the
  standard registries; look them up in the vendor's message registry.
