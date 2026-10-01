# Citation grading sheet

From `eval/reports/a6/dev/r0-baseline.json`. Grade each passage: does it support the diagnosis (its root cause given the observations, or its recommended action)? Judge verdicts are not shown.

## a6dev/f021-pcie_degradation

**Root cause:** unknown · **Action:** none

**Summary:** High PCIe replays (320-813 in 10-min intervals) observed on GPU 2 without XID errors, BMC logs, or hardware faults. GPU temperature (28°C), power (70W), and utilization (0%) were normal, indicating no heavy workload or thermal issue. Runbooks show rising PCIe replays often precede XID 79, but no XID occurred here. Without hardware errors, this is likely a false positive detector trigger (not pcie_degradation).

**Rationale:** No hardware errors or workload issues observed; detector likely triggered on transient noise. No action needed beyond monitoring.

### `inc-2025031#report`

> GPU 6 fell off the bus on h100-node-04 > Report
>
> Summary: BMC PCIe fatal error on GPU slot 6 followed by XID 79; lspci showed the device missing. Severity sev1. Resolved in 31.3 hours.
>
> Timeline: - 2025-06-15 07:36 UTC: BMC PCIe fatal error on GPU slot 6 followed by XID 79; lspci showed the device missing.
> - 08:12 UTC: on-call acknowledged and started diagnosis.
> - 2025-06-16 14:53 UTC: resolved.
>
> Root cause: Faulty PCIe riser for GPU 6.
>
> Resolution: Node rebooted, riser replaced after a second XID 79 a week later.
>
> Lessons: PCIe replays had been rising on this GPU for days before the first XID 79.

### `inc-2025052#report`

> GPU 7 fell off the bus on h100-node-03 > Report
>
> Summary: XID 79 on GPU 7; the GPU stopped reporting and all jobs on h100-node-03 failed. Severity sev1. Resolved in 8.4 hours.
>
> Timeline: - 2025-01-17 03:55 UTC: XID 79 on GPU 7; the GPU stopped reporting and all jobs on h100-node-03 failed.
> - 04:07 UTC: on-call acknowledged and started diagnosis.
> - 2025-01-17 12:16 UTC: resolved.
>
> Root cause: Faulty PCIe riser for GPU 7.
>
> Resolution: Node rebooted, riser replaced after a second XID 79 a week later.
>
> Lessons: PCIe replays had been rising on this GPU for days before the first XID 79.

### `inc-2025010#report`

> GPU 0 fell off the bus on a100-node-03 > Report
>
> Summary: XID 79 on GPU 0; the GPU stopped reporting and all jobs on a100-node-03 failed. Severity sev1. Resolved in 10.6 hours.
>
> Timeline: - 2025-03-24 23:53 UTC: XID 79 on GPU 0; the GPU stopped reporting and all jobs on a100-node-03 failed.
> - 23:58 UTC: on-call acknowledged and started diagnosis.
> - 2025-03-25 10:29 UTC: resolved.
>
> Root cause: Faulty PCIe riser for GPU 0.
>
> Resolution: Node rebooted, riser replaced after a second XID 79 a week later.
>
> Lessons: PCIe replays had been rising on this GPU for days before the first XID 79.

## a6dev/f012-nvlink_degradation

**Root cause:** nvlink_degradation · **Action:** drain_node

**Summary:** NVLink CRC errors on GPUs 2 and 3 (3986/3927 total) with XID 74 error match nvlink_degradation symptoms. No thermal/power issues (temps <65°C, normal power utilization), and no other errors in logs. Affected GPUs show isolated CRC spikes while neighbors operate normally.

**Rationale:** Per runbook remediation, degraded NVLink links require node drain to prevent performance impact on all multi-GPU jobs using the affected pair. Resetting GPUs (rb-010-gpu-reset) is recommended but requires human approval via drain_node request.

### `rb-005-nvlink-degradation#symptoms`

> NVLink degradation > Symptoms
>
> - NVLink CRC (flit or data) error counters climb on two GPUs that share a link, from a few per
>   minute to hundreds.
> - XID 74 (NVLink error) in the kernel log.
> - Multi-GPU training slows down: all-reduce time rises and GPU utilization drops on the affected
>   pair (and often the whole job), because links retry or retrain.
> - Severe cases: link goes down and NCCL jobs fail or fall back to a slower path.

### `rb-005-nvlink-degradation#remediation`

> NVLink degradation > Remediation
>
> - Drain the node (rb-009-node-drain); degraded links slow every job that uses them.
> - Reset the GPUs (rb-010-gpu-reset) or reboot the node to retrain the links. On NVSwitch systems,
>   reset the whole baseboard rather than single GPUs.
> - If errors return after a reboot, the baseboard, NVSwitch, or a GPU needs service: open an RMA
>   with the error counters and XID log attached (rb-011-rma-process).

## a6dev/f023-power_fault

**Root cause:** noisy_neighbor · **Action:** none

**Summary:** All GPUs (0-7) showed enforced power limit reduction to 420W (nominal 700W) during high utilization (90-95%), with normal temperatures (60-70°C) and no hardware errors (XID, ECC, PCIe, NVLink, or BMC faults). The power reduction aligns with workload behavior (z-score power values of -5.2 to -5.5), not hardware failure. Runbook rb-015 confirms that without hardware errors or thermal issues, this is a noisy_neighbor scenario.

**Rationale:** No hardware errors or thermal issues observed; power capping is consistent with heavy workload behavior (noisy_neighbor). No action required beyond monitoring.

### `rb-015-power-capping-policy#when-nodes-get-capped`

> Power capping policy > When nodes get capped
>
> - Automatically by the BMC or node manager when PSU redundancy is lost (rb-003-power-fault).
> - Manually during facility power or cooling events, by the on-call lead.

### `rb-015-power-capping-policy#rules`

> Power capping policy > Rules
>
> - Record every manual cap in the incident with a reason and an end condition.
> - Remove caps as soon as the condition ends; a forgotten cap looks like a power fault later.
> - If an enforced limit stays low after PSU redundancy is restored, check the BMC power policy
>   (Redfish `PowerSubsystem` / vendor node-manager settings) before blaming the GPUs.

## a6dev/f022-gpu_off_bus

**Root cause:** gpu_off_bus · **Action:** drain_node

**Summary:** GPU 0 fell off the bus (XID 79) at 2026-09-30T14:05:09Z, confirmed by BMC PCIe fatal error. Telemetry shows GPU 0 stopped reporting (last sample at 14:04:00Z) while other GPUs remained active with normal metrics (temp 63.6°C, power 377W, no throttling). No thermal or power anomalies (temp < slowdown limit, power within limits), ruling out thermal_runaway or power_fault. Runbook confirms XID 79 + PCIe error = gpu_off_bus. No evidence of driver bug or workload behavior (other GPUs healthy).

**Rationale:** GPU 0 is off the bus (XID 79), causing job failures. Node must be drained for hardware inspection. Hardware fault (PCIe link failure) confirmed by BMC log.

### `rb-004-gpu-off-bus#symptoms`

> GPU fell off the bus (XID 79) > Symptoms
>
> - Kernel log: `NVRM: Xid (PCI:0000:xx:00): 79, GPU has fallen off the bus.`
> - The GPU disappears from telemetry: no samples, `nvidia-smi` reports "Unable to determine the
>   device handle" or hangs.
> - Often a BMC critical-interrupt entry for a PCIe fatal error on the same slot.
> - Every job using that GPU fails. Multi-GPU jobs on the node usually fail too.

### `rb-004-gpu-off-bus#likely-causes`

> GPU fell off the bus (XID 79) > Likely causes
>
> - PCIe link failure (riser, retimer, cable, or slot problem).
> - GPU hardware fault or GPU power delivery problem.
> - Severe overheating that tripped GPU protection (check thermal history first).
> - Less often a driver bug; look for a known issue for the driver version in use.

## a6dev/f020-gpu_off_bus

**Root cause:** gpu_off_bus · **Action:** drain_node

**Summary:** GPU 6 fell off the bus (XID 79) with no thermal, power, or PCIe errors. Telemetry shows GPU 6 stopped reporting (174 samples vs 282 for others) at 2026-09-30T14:50:00Z, matching XID 79 error at 2026-09-30T14:51:09Z. Temperatures (31-33°C) and power (55W) were normal, ruling out thermal overload or workload behavior. Runbook confirms XID 79 is a hardware fault symptom requiring node drain.

**Rationale:** XID 79 indicates GPU hardware fault. Runbook remediation requires draining the node (rb-009-node-drain) before rebooting, as GPU cannot recover while OS is running.

### `rb-004-gpu-off-bus#symptoms`

> GPU fell off the bus (XID 79) > Symptoms
>
> - Kernel log: `NVRM: Xid (PCI:0000:xx:00): 79, GPU has fallen off the bus.`
> - The GPU disappears from telemetry: no samples, `nvidia-smi` reports "Unable to determine the
>   device handle" or hangs.
> - Often a BMC critical-interrupt entry for a PCIe fatal error on the same slot.
> - Every job using that GPU fails. Multi-GPU jobs on the node usually fail too.

### `rb-004-gpu-off-bus#remediation`

> GPU fell off the bus (XID 79) > Remediation
>
> 1. Drain the node (rb-009-node-drain). The GPU can't be recovered while the OS is running.
> 2. Reboot the node; a GPU reset is not enough after XID 79.
> 3. If the GPU returns, run DCGM diagnostics level 3 (rb-012-dcgm-diagnostics) before returning the
>    node to service.
> 4. A second XID 79 on the same GPU within 30 days: reseat the GPU or riser; if it happens again,
>    RMA (rb-011-rma-process).

## a6dev/f024-thermal_runaway

**Root cause:** thermal_runaway · **Action:** monitor

**Summary:** BMC logs show FAN2 failed (0 RPM) at 18:32:29Z, causing GPUs 0-3 to overheat (max 86°C) while GPUs 4-7 remained cool (max 65°C). Thermal residuals (+12-13°C) and throttling (SM clocks dropped 30-45%) confirm thermal runaway. Runbook rb-001-thermal-runaway#symptoms matches: BMC fan sensor failure, GPU temps climbing with power flat, and throttling. No hardware errors (XID, ECC, PCIe) or facility issues (neighboring GPUs normal).

**Rationale:** Fan recovered at 18:53:59Z (BMC log), and throttling ceased. No persistent hardware fault detected; monitor for recurrence after fan replacement.

### `rb-001-thermal-runaway#symptoms`

> Thermal runaway and fan failure > Symptoms
>
> - BMC SEL entry for a fan sensor crossing a critical low threshold, often with a reading of 0 RPM
>   (Redfish message `SensorThresholdCriticalLowGoingLow`).
> - GPU temperatures in one cooling zone climb over several minutes while power stays flat or drops.
> - `hw_thermal_slowdown` / `sw_thermal_slowdown` throttle reasons appear and SM clocks fall 30-45%.
> - Jobs on the affected GPUs slow down; training step time rises on every rank that shares the node.
> - A GPU running more than about 5 C hotter than its power draw predicts is the earliest telemetry
>   sign, before any throttling.

### `rb-001-thermal-runaway#diagnosis`

> Thermal runaway and fan failure > Diagnosis
>
> 1. Read the fan sensors: `ipmitool sdr type Fan`, or Redfish
>    `GET /redfish/v1/Chassis/<id>/ThermalSubsystem/Fans`. A fan at 0 RPM or far below its peers
>    confirms the fault.
> 2. Check which GPUs are affected. A cooling zone usually covers four GPUs (0-3 or 4-7 on 8-GPU
>    baseboards). If only one GPU is hot, suspect that GPU's heatsink rather than a fan.
> 3. Compare inlet temperature with neighbouring nodes. If the whole rack is warm, the problem is
>    facility cooling, not this node.
> 4. Confirm throttling with `nvidia-smi -q -d PERFORMANCE` (look at Clocks Event Reasons).

## a6dev/f017-ecc_degradation

**Root cause:** ecc_degradation · **Action:** drain_node

**Summary:** GPU 3 on a6dev-h100-node-05 showed accelerating correctable ECC errors (SBE) from 5-6 per 30 min to 51-53 per 30 min, followed by an uncorrectable error (DBE) with XID 48 (evidence ev-108-110, ev-183-184). BMC inlet temperature warning (36°C vs 35°C threshold) was minor and unrelated to GPU thermal behavior (metrics show 68-71°C GPU temps, within normal range for heavy workloads). No hardware errors beyond ECC, confirming ecc_degradation per symptoms in runbook rb-002-ecc-degradation#symptoms.

**Rationale:** XID 48 (DBE) confirms hardware degradation; per runbook escalation, sev1 incidents with DBE require draining the node to prevent job crashes.

### `rb-002-ecc-degradation#symptoms`

> ECC error degradation (correctable to uncorrectable) > Symptoms
>
> - Correctable (single-bit, SBE) ECC errors on one GPU rise from near zero to dozens per hour.
>   Healthy HBM sees roughly one correctable error per GPU per day or less.
> - Retired pages (Volta and older) or remapped rows (Ampere and Hopper) increase.
> - XID 92 (high single-bit ECC error rate) may appear.
> - The failure often ends in an uncorrectable (double-bit, DBE) error with XID 48, which kills the
>   running job. On Ampere and Hopper, XID 94 (contained) or XID 95 (uncontained) can follow.

### `rb-002-ecc-degradation#escalation`

> ECC error degradation (correctable to uncorrectable) > Escalation
>
> - Sev3 while only correctable errors are rising; sev2 once the rate exceeds 50 per 30 minutes;
>   sev1 on DBE or XID 95.

## a6dev/f016-pcie_degradation

**Root cause:** pcie_degradation · **Action:** drain_node

**Summary:** GPU 3 on node a6dev-a100-node-02 shows steadily increasing PCIe replays (208 → 1567 in 33 minutes) without thermal/power anomalies or hardware errors. Temperatures (60-65°C) and power usage (330-390W) are within normal ranges for heavy workloads. No XID errors or BMC logs found. Matches runbook symptoms: 'PCIe replay counter increases steadily' (rb-006-pcie-degradation#symptoms).

**Rationale:** PCIe replays without hardware errors require maintenance (rb-006-pcie-degradation#remediation). Schedule drain for GPU reseating/replacement.

### `rb-006-pcie-degradation#diagnosis`

> PCIe link degradation and replays > Diagnosis
>
> 1. Check the replay counter: `nvidia-smi -q` (PCI section, "Replays Since Reset"), or DCGM field
>    `DCGM_FI_DEV_PCIE_REPLAY_COUNTER`.
> 2. Check the trained link: `lspci -vvv -s <bus-id> | grep -E 'LnkCap|LnkSta'`. LnkSta should match
>    LnkCap (for example x16 at 32 GT/s for PCIe Gen5).
> 3. Look for AER messages: `journalctl -k | grep -i aer`.
> 4. Check whether other GPUs behind the same PCIe switch show replays. Several GPUs on one switch
>    point at the switch or retimer, not the GPU.

### `rb-006-pcie-degradation#remediation`

> PCIe link degradation and replays > Remediation
>
> - Replays without errors are not urgent, but they often come before a GPU falls off the bus
>   (rb-004-gpu-off-bus). Schedule a drain.
> - Reseat the GPU and riser or cable during the maintenance window, then confirm the link trains at
>   full width and speed and the counter stays flat under load (rb-018-burn-in-after-repair).
> - If replays persist after reseating, replace the riser or cable, then the GPU (rb-011-rma-process).

## a6dev/f008-power_fault

**Root cause:** power_fault · **Action:** monitor

**Summary:** BMC log showed PSU1 input lost (ev-0), triggering automatic power capping across all GPUs. All GPUs exhibited enforced power limits at 240W (vs. nominal 400W), with high utilization (90-93%) and temps within safe range (56-58°C). No hardware errors (XID, ECC, PCIe/NVLink errors) or temperatures beyond slowdown limits. Root cause is PSU redundancy loss (rb-015-power-capping-policy#when-nodes-get-capped), not GPU fault. GPU 7 (55W, 0% utilization) was inactive but flagged due to node-wide cap.

**Rationale:** Automatic power capping due to PSU fault (rb-015) will resolve when PSU is fixed. No GPU hardware failure detected; monitoring ensures cap lifts without intervention.

### `rb-015-power-capping-policy#when-nodes-get-capped`

> Power capping policy > When nodes get capped
>
> - Automatically by the BMC or node manager when PSU redundancy is lost (rb-003-power-fault).
> - Manually during facility power or cooling events, by the on-call lead.

### `rb-013-bmc-redfish-triage#interpreting-entries`

> Reading BMC logs (Redfish and IPMI SEL) > Interpreting entries
>
> - An `Assert` followed later by a `Deassert` for the same sensor is a condition that started and
>   cleared. The time between them is how long it lasted.
> - Fan `SensorThresholdCriticalLowGoingLow` with reading 0: fan failure (rb-001-thermal-runaway).
> - `ResourceErrorsDetected` on a PSU: power supply problem (rb-003-power-fault).
> - Critical interrupt / PCIe fatal error on a GPU slot: usually paired with XID 79
>   (rb-004-gpu-off-bus).
> - Inlet temperature warnings that clear within minutes: usually facility airflow, low priority
>   unless frequent (rb-014-inlet-temperature).

## a6dev/f011-power_fault

**Root cause:** noisy_neighbor · **Action:** monitor

**Summary:** All 8 GPUs on a6dev-h100-node-05 showed elevated power (507-517W avg) and utilization (91-94%) with enforced power limits reduced to 420W (nominal 700W), but no hardware errors (XID, ECC, PCIe/NVLink errors, or sustained thermal issues). BMC inlet temperature warning (36.1°C) resolved within 4 minutes. Evidence shows consistent workload shift (z-scores -4.7 to -4.9) without thermal residual or hardware faults, matching runbook criteria for noisy_neighbor.

**Rationale:** No hardware errors detected; workload shift confirmed by power z-scores and lack of thermal residual. Per runbook, never drain for high utilization alone; monitor for 30 minutes to confirm stability.

### `rb-007-noisy-neighbor-triage#when-to-use-this`

> Workload change or hardware fault? (noisy neighbor triage) > When to use this
>
> A GPU or group of GPUs suddenly shows high utilization, power, and temperature, but there are no
> hardware errors: no XIDs, no ECC or PCIe/NVLink counter increases, no BMC warnings. This is
> usually a workload change, such as a new job, a noisy neighbor sharing the node, or a job moving
> to these GPUs, not a fault.

### `rb-007-noisy-neighbor-triage#what-to-do`

> Workload change or hardware fault? (noisy neighbor triage) > What to do
>
> - Workload shift confirmed: no hardware action. If the neighbor is degrading another tenant's job,
>   that's a scheduling issue: check GPU isolation (MIG or exclusive mode) and raise it with the
>   platform team.
> - Uncertain: keep watching for 30 minutes. A real fault produces error signals or a growing thermal
>   residual; a workload shift stays explained by power.
> - Never drain a node just because utilization is high.

### `rb-022-detector-alerts-guide#model-alerts`

> What the detector's alerts mean > Model alerts
>
> - `[thermal_residual]`: the GPU is hotter than its power draw predicts, by 5 C or more for 3
>   minutes. This is the earliest sign of a cooling problem, often before any throttling.
> - `[workload_shift]`: a big change in temperature, power, or utilization that the power draw
>   explains. Usually a new job or noisy neighbor (rb-007-noisy-neighbor-triage). Informational.

## a6dev/f001-ecc_degradation

**Root cause:** ecc_degradation · **Action:** drain_node

**Summary:** GPU 0 on a6dev-a100-node-01 showed accelerating correctable ECC errors (SBE) from 5-50 per 30 min, peaking at 312 total before an uncorrectable error (DBE) with XID 48. Metrics confirm rising SBE rate (e.g., 235 SBE at 09:45, 312 at 10:00) and retired pages (ev-66, ev-93, ev-111), matching runbook symptoms for ecc_degradation. No thermal/power issues beyond normal workload (temp max 67°C, power 380W). BMC inlet warning (36.2°C) was below critical threshold (35°C) and unrelated.

**Rationale:** DBE (XID 48) confirms hardware degradation; runbook escalation requires sev1 action. GPU must be drained to prevent job crashes and enable RMA.

### `rb-002-ecc-degradation#symptoms`

> ECC error degradation (correctable to uncorrectable) > Symptoms
>
> - Correctable (single-bit, SBE) ECC errors on one GPU rise from near zero to dozens per hour.
>   Healthy HBM sees roughly one correctable error per GPU per day or less.
> - Retired pages (Volta and older) or remapped rows (Ampere and Hopper) increase.
> - XID 92 (high single-bit ECC error rate) may appear.
> - The failure often ends in an uncorrectable (double-bit, DBE) error with XID 48, which kills the
>   running job. On Ampere and Hopper, XID 94 (contained) or XID 95 (uncontained) can follow.

### `rb-002-ecc-degradation#diagnosis`

> ECC error degradation (correctable to uncorrectable) > Diagnosis
>
> 1. Check counters: `nvidia-smi -q -d ECC` (volatile and aggregate SBE/DBE counts).
> 2. On A100/H100, check row remapping:
>    `nvidia-smi --query-remapped-rows=gpu_bus_id,remapped_rows.correctable,remapped_rows.uncorrectable,remapped_rows.pending,remapped_rows.failure --format=csv`.
>    See rb-021-hbm-row-remapping for how to read the result.
> 3. Look at the rate, not the total. Counters are cumulative and reset on driver reload, so compare
>    increases over time windows.
> 4. Search past incidents for the same GPU serial. Repeated ECC incidents on one GPU are an RMA signal.

## a6dev/f018-ecc_degradation

**Root cause:** noisy_neighbor · **Action:** none

**Summary:** GPU 4 and 6 showed significantly lower temperature (41.4°C vs 63°C avg), power (183W vs 373W avg), and utilization (37% vs 92%) compared to neighbors during the incident window. No hardware errors (XID, ECC, PCIe/NVLink counters unchanged, BMC logs clean). Evidence matches `workload_shift` signal in detector, confirming a workload change (noisy neighbor) rather than hardware fault. Runbook rb-007-noisy-neighbor-triage confirms this is a normal workload shift requiring no action.

**Rationale:** Workload shift confirmed by metrics and detector signal; no hardware errors observed. Runbook explicitly states no action required for confirmed workload shifts.

### `rb-007-noisy-neighbor-triage#when-to-use-this`

> Workload change or hardware fault? (noisy neighbor triage) > When to use this
>
> A GPU or group of GPUs suddenly shows high utilization, power, and temperature, but there are no
> hardware errors: no XIDs, no ECC or PCIe/NVLink counter increases, no BMC warnings. This is
> usually a workload change, such as a new job, a noisy neighbor sharing the node, or a job moving
> to these GPUs, not a fault.

### `rb-007-noisy-neighbor-triage#what-to-do`

> Workload change or hardware fault? (noisy neighbor triage) > What to do
>
> - Workload shift confirmed: no hardware action. If the neighbor is degrading another tenant's job,
>   that's a scheduling issue: check GPU isolation (MIG or exclusive mode) and raise it with the
>   platform team.
> - Uncertain: keep watching for 30 minutes. A real fault produces error signals or a growing thermal
>   residual; a workload shift stays explained by power.
> - Never drain a node just because utilization is high.

### `rb-022-detector-alerts-guide#model-alerts`

> What the detector's alerts mean > Model alerts
>
> - `[thermal_residual]`: the GPU is hotter than its power draw predicts, by 5 C or more for 3
>   minutes. This is the earliest sign of a cooling problem, often before any throttling.
> - `[workload_shift]`: a big change in temperature, power, or utilization that the power draw
>   explains. Usually a new job or noisy neighbor (rb-007-noisy-neighbor-triage). Informational.

## a6dev/f000-pcie_degradation

**Root cause:** pcie_degradation · **Action:** drain_node

**Summary:** GPU 0 on node a6dev-h100-node-01 showed rapidly increasing PCIe replay counter (270→3066 in 10 minutes), matching runbook symptoms of PCIe degradation. No XID errors or BMC logs found. Temperatures (37-41°C) and power usage (190-265W) were normal, ruling out thermal or power issues. The replay counter increase (vs. baseline of 2-34) aligns with runbook symptom 1 and remediation guidance to schedule a drain.

**Rationale:** PCIe replay counter exceeded threshold (3066 in 10 min) with no hardware errors, per runbook remediation: 'Replays without errors are not urgent, but they often come before a GPU falls off the bus. Schedule a drain.'

### `rb-006-pcie-degradation#symptoms`

> PCIe link degradation and replays > Symptoms
>
> - The GPU's PCIe replay counter increases steadily (tens to hundreds per minute). Healthy links
>   stay at or near zero.
> - Host-to-device transfer bandwidth drops; data loading becomes a bottleneck and GPU utilization
>   falls slightly (around 10%).
> - Correctable AER errors in the kernel log for the GPU's PCIe path.
> - The link may have trained at a lower width or speed than it supports.

### `rb-006-pcie-degradation#remediation`

> PCIe link degradation and replays > Remediation
>
> - Replays without errors are not urgent, but they often come before a GPU falls off the bus
>   (rb-004-gpu-off-bus). Schedule a drain.
> - Reseat the GPU and riser or cable during the maintenance window, then confirm the link trains at
>   full width and speed and the counter stays flat under load (rb-018-burn-in-after-repair).
> - If replays persist after reseating, replace the riser or cable, then the GPU (rb-011-rma-process).

## a6dev/f003-nvlink_degradation

**Root cause:** nvlink_degradation · **Action:** drain_node

**Summary:** GPU 4 and 5 showed sustained NVLink CRC error increases (4767 and 4640 over 65 minutes) with XID 74 error, matching runbook symptoms of degraded NVLink links. No thermal/power anomalies or ECC errors. Affected GPUs share a link (per runbook diagnosis), and other GPUs show normal metrics.

**Rationale:** Degraded NVLink links slow all jobs using the affected GPUs; drain_node is required per runbook remediation to prevent job performance degradation until hardware is serviced.

### `rb-005-nvlink-degradation#symptoms`

> NVLink degradation > Symptoms
>
> - NVLink CRC (flit or data) error counters climb on two GPUs that share a link, from a few per
>   minute to hundreds.
> - XID 74 (NVLink error) in the kernel log.
> - Multi-GPU training slows down: all-reduce time rises and GPU utilization drops on the affected
>   pair (and often the whole job), because links retry or retrain.
> - Severe cases: link goes down and NCCL jobs fail or fall back to a slower path.

### `rb-005-nvlink-degradation#remediation`

> NVLink degradation > Remediation
>
> - Drain the node (rb-009-node-drain); degraded links slow every job that uses them.
> - Reset the GPUs (rb-010-gpu-reset) or reboot the node to retrain the links. On NVSwitch systems,
>   reset the whole baseboard rather than single GPUs.
> - If errors return after a reboot, the baseboard, NVSwitch, or a GPU needs service: open an RMA
>   with the error counters and XID log attached (rb-011-rma-process).

## a6dev/f013-ecc_degradation

**Root cause:** ecc_degradation · **Action:** drain_node

**Summary:** GPU 6 on a6dev-h100-node-04 showed accelerating correctable ECC errors (SBE) from 5 to 52 per 30 minutes, culminating in a DBE (XID 48) and 1 uncorrectable error (ev-175). Metrics show no thermal/power issues (temp max 71°C, power 650-680W < 700W limit), no PCIe/NVLink errors, and consistent workload (utilization 88-97%). The trend matches ecc_degradation runbook (rb-002-ecc-degradation#diagnosis), with DBE triggering sev1 (rb-002-ecc-degradation#escalation).

**Rationale:** DBE (XID 48) confirms hardware degradation; runbook requires sev1 escalation (rb-002-ecc-degradation#escalation). GPU must be taken offline for RMA.

### `rb-002-ecc-degradation#escalation`

> ECC error degradation (correctable to uncorrectable) > Escalation
>
> - Sev3 while only correctable errors are rising; sev2 once the rate exceeds 50 per 30 minutes;
>   sev1 on DBE or XID 95.

### `rb-002-ecc-degradation#diagnosis`

> ECC error degradation (correctable to uncorrectable) > Diagnosis
>
> 1. Check counters: `nvidia-smi -q -d ECC` (volatile and aggregate SBE/DBE counts).
> 2. On A100/H100, check row remapping:
>    `nvidia-smi --query-remapped-rows=gpu_bus_id,remapped_rows.correctable,remapped_rows.uncorrectable,remapped_rows.pending,remapped_rows.failure --format=csv`.
>    See rb-021-hbm-row-remapping for how to read the result.
> 3. Look at the rate, not the total. Counters are cumulative and reset on driver reload, so compare
>    increases over time windows.
> 4. Search past incidents for the same GPU serial. Repeated ECC incidents on one GPU are an RMA signal.

## a6dev/f009-thermal_runaway

**Root cause:** thermal_runaway · **Action:** monitor

**Summary:** GPU 5-7 showed sustained thermal residuals (+13-14°C above predicted by power), max temps 88-89°C, and thermal throttling (3/6 samples at 88°C), matching runbook symptoms. No XID errors or BMC logs confirmed hardware faults. Neighboring GPUs (0-4) remained cool (31-70°C), ruling out facility cooling. Degraded thermal interface material on GPUs 5-7 is the likely cause.

**Rationale:** Thermal runaway symptoms match telemetry without hardware errors. No need for drain/reset; monitor for recurrence as degraded thermal interface material may require maintenance.

### `rb-001-thermal-runaway#symptoms`

> Thermal runaway and fan failure > Symptoms
>
> - BMC SEL entry for a fan sensor crossing a critical low threshold, often with a reading of 0 RPM
>   (Redfish message `SensorThresholdCriticalLowGoingLow`).
> - GPU temperatures in one cooling zone climb over several minutes while power stays flat or drops.
> - `hw_thermal_slowdown` / `sw_thermal_slowdown` throttle reasons appear and SM clocks fall 30-45%.
> - Jobs on the affected GPUs slow down; training step time rises on every rank that shares the node.
> - A GPU running more than about 5 C hotter than its power draw predicts is the earliest telemetry
>   sign, before any throttling.

### `rb-001-thermal-runaway#diagnosis`

> Thermal runaway and fan failure > Diagnosis
>
> 1. Read the fan sensors: `ipmitool sdr type Fan`, or Redfish
>    `GET /redfish/v1/Chassis/<id>/ThermalSubsystem/Fans`. A fan at 0 RPM or far below its peers
>    confirms the fault.
> 2. Check which GPUs are affected. A cooling zone usually covers four GPUs (0-3 or 4-7 on 8-GPU
>    baseboards). If only one GPU is hot, suspect that GPU's heatsink rather than a fan.
> 3. Compare inlet temperature with neighbouring nodes. If the whole rack is warm, the problem is
>    facility cooling, not this node.
> 4. Confirm throttling with `nvidia-smi -q -d PERFORMANCE` (look at Clocks Event Reasons).

## a6dev/f014-nvlink_degradation

**Root cause:** noisy_neighbor · **Action:** monitor

**Summary:** GPU 2 showed high utilization (86%) and power (620W) during the incident window, consistent with a node-wide workload shift. No hardware errors (XID, ECC, PCIe/NVLink, BMC) were detected in logs. Other GPUs (0,5,6,7) also exhibited high utilization (95-98%) and power (670-680W), confirming a workload change rather than a hardware fault. The runbook confirms that high utilization without hardware errors indicates a noisy neighbor (workload behavior).

**Rationale:** Workload behavior confirmed; no hardware failure detected. Monitor for recurrence to confirm if workload shift is transient.

### `rb-007-noisy-neighbor-triage#when-to-use-this`

> Workload change or hardware fault? (noisy neighbor triage) > When to use this
>
> A GPU or group of GPUs suddenly shows high utilization, power, and temperature, but there are no
> hardware errors: no XIDs, no ECC or PCIe/NVLink counter increases, no BMC warnings. This is
> usually a workload change, such as a new job, a noisy neighbor sharing the node, or a job moving
> to these GPUs, not a fault.

## a6dev/f010-gpu_off_bus

**Root cause:** gpu_off_bus · **Action:** drain_node

**Summary:** GPU 0 fell off the bus (XID 79), confirmed by kernel log and telemetry showing zero samples for GPU 0 while other GPUs remained active. Temperatures (63°C) were within normal range, ruling out thermal runaway. No PCIe/NVLink errors or BMC faults observed. Runbook confirms XID 79 indicates PCIe/hardware failure requiring node drain.

**Rationale:** XID 79 requires node drain per runbook (rb-004-gpu-off-bus#remediation); GPU cannot recover while OS is running. Reboot needed after drain.

### `rb-004-gpu-off-bus#symptoms`

> GPU fell off the bus (XID 79) > Symptoms
>
> - Kernel log: `NVRM: Xid (PCI:0000:xx:00): 79, GPU has fallen off the bus.`
> - The GPU disappears from telemetry: no samples, `nvidia-smi` reports "Unable to determine the
>   device handle" or hangs.
> - Often a BMC critical-interrupt entry for a PCIe fatal error on the same slot.
> - Every job using that GPU fails. Multi-GPU jobs on the node usually fail too.

### `rb-004-gpu-off-bus#remediation`

> GPU fell off the bus (XID 79) > Remediation
>
> 1. Drain the node (rb-009-node-drain). The GPU can't be recovered while the OS is running.
> 2. Reboot the node; a GPU reset is not enough after XID 79.
> 3. If the GPU returns, run DCGM diagnostics level 3 (rb-012-dcgm-diagnostics) before returning the
>    node to service.
> 4. A second XID 79 on the same GPU within 30 days: reseat the GPU or riser; if it happens again,
>    RMA (rb-011-rma-process).

## a6dev/f004-thermal_runaway

**Root cause:** thermal_runaway · **Action:** drain_node

**Summary:** BMC log shows FAN3 at 0 RPM (critical low threshold) at 2026-10-01T15:55:09Z, causing thermal runaway in GPUs 5-7. Metrics confirm GPUs 5-7 are 11-12.9°C hotter than predicted by power draw (ev-2, ev-3, ev-4), with throttling events (ev-10, ev-12, ev-18) and temperatures exceeding 83°C (ev-1, ev-5, ev-8). Neighbouring GPUs (0-4) show normal temps (31-72°C), confirming localized cooling failure. Runbook rb-001-thermal-runaway#symptoms matches all symptoms: BMC fan error, thermal residuals, throttling.

**Rationale:** BMC fan failure (FAN3 at 0 RPM) is confirmed as the root cause. Hardware failure requires node drainage for fan replacement. Runbook rb-001-thermal-runaway#escalation specifies Sev2 escalation for active throttling on production jobs.

### `rb-001-thermal-runaway#symptoms`

> Thermal runaway and fan failure > Symptoms
>
> - BMC SEL entry for a fan sensor crossing a critical low threshold, often with a reading of 0 RPM
>   (Redfish message `SensorThresholdCriticalLowGoingLow`).
> - GPU temperatures in one cooling zone climb over several minutes while power stays flat or drops.
> - `hw_thermal_slowdown` / `sw_thermal_slowdown` throttle reasons appear and SM clocks fall 30-45%.
> - Jobs on the affected GPUs slow down; training step time rises on every rank that shares the node.
> - A GPU running more than about 5 C hotter than its power draw predicts is the earliest telemetry
>   sign, before any throttling.

### `rb-001-thermal-runaway#likely-causes`

> Thermal runaway and fan failure > Likely causes
>
> - Failed or failing chassis fan (bearing failure, connector, fan controller).
> - Blocked airflow: missing blanking panel, cable bundle in front of the intake, clogged filter.
> - Hot aisle containment breach or CRAC unit failure (affects many nodes at once; see
>   rb-014-inlet-temperature).
> - Degraded thermal interface material on a single GPU (one GPU hot, its neighbours normal).

## a6dev/f015-thermal_runaway

**Root cause:** thermal_runaway · **Action:** drain_node

**Summary:** BMC logs show FAN4 failed at 0 RPM (critical), causing inlet temperature warning (36.5°C vs 35°C threshold). GPU telemetry shows no overheating (max temp 34.6°C, no throttling), confirming thermal runaway was not triggered but the fan failure is the root cause. Runbook cites fan failure as a likely cause for thermal runaway (rb-001-thermal-runaway#likely-causes).

**Rationale:** Fan failure (FAN4 at 0 RPM) requires node drain before hardware replacement per runbook remediation (rb-001-thermal-runaway#remediation).

### `rb-001-thermal-runaway#remediation`

> Thermal runaway and fan failure > Remediation
>
> - Drain the node before replacing a fan (rb-009-node-drain). Running at thermal slowdown risks
>   job timeouts and long-term GPU wear.
> - Replace the failed fan module; most are hot-swappable, but follow the chassis service guide.
> - After replacement, confirm all fans report normal RPM and GPU temperatures return to their
>   usual range under load, then run the burn-in in rb-018-burn-in-after-repair.
> - If temperatures stay high with all fans healthy, open a hardware ticket for heatsink or TIM
>   inspection.
