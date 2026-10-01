"""Generate synthetic past-incident reports into knowledge/incidents/ (seeded, reproducible).

These stand in for a real team's closed incident tickets: what was seen, what the root cause
turned out to be, what fixed it. They're the "has this happened before?" half of the knowledge base.

    uv run python scripts/generate_incident_reports.py --count 64 --seed 7
"""

import argparse
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent.parent / "knowledge" / "incidents"
NODES = [f"h100-node-0{i}" for i in range(1, 6)] + [f"a100-node-0{i}" for i in range(1, 4)]


@dataclass(frozen=True)
class Scenario:
    failure_type: str
    components: str
    title: str
    symptoms: list[str]
    causes: list[tuple[str, str, str]]  # (root cause, resolution, lesson)
    hours: tuple[float, float]  # time to resolve


SCENARIOS = [
    Scenario(
        "thermal_runaway",
        "fan, gpu, node",
        "Thermal throttling on {node} GPUs {zone}",
        [
            "BMC reported {fan} at 0 RPM; GPUs {zone} reached {temp} C and entered thermal slowdown.",
            "Training step time rose {pct}% on a job using {node}; GPUs {zone} showed hw_thermal_slowdown.",
            "Detector flagged GPUs {zone} running {resid} C hotter than their power predicts; no BMC entry was collected.",
        ],
        [
            (
                "Failed fan module {fan} (bearing failure).",
                "Drained the node, hot-swapped {fan}, burn-in passed.",
                "The thermal residual alert fired before throttling started; acting on it saves a job restart.",
            ),
            (
                "Fan connector for {fan} had worked loose after a previous service visit.",
                "Reseated the connector; fan back to normal RPM.",
                "After any chassis service, check all fan readings before resuming the node.",
            ),
            (
                "Blanking panel missing above the node, recirculating hot air into {fan}'s zone.",
                "Installed the blanking panel; temperatures normal under load.",
                "Fans were healthy; compare inlet temperature with neighbours before replacing parts.",
            ),
        ],
        (1, 6),
    ),
    Scenario(
        "ecc_degradation",
        "gpu, hbm",
        "Rising ECC errors on {node} GPU {gpu}",
        [
            "Correctable ECC errors on GPU {gpu} rose to {sbe} in 30 minutes; remapped rows increased.",
            "XID 48 (double-bit ECC error) on GPU {gpu} killed a training job after a day of rising correctable errors.",
            "XID 92 (high single-bit ECC error rate) on GPU {gpu}; {sbe} correctable errors in the last hour.",
        ],
        [
            (
                "Degrading HBM stack on GPU {gpu} (serial {serial}).",
                "Reset applied pending row remaps; errors resumed within 2 days; GPU replaced under RMA.",
                "Errors resuming after a reset is the RMA signal; don't keep resetting.",
            ),
            (
                "Single bad row on GPU {gpu}; pending remap had not been applied.",
                "Drained and reset the GPU; remap applied; no further errors over 30 days.",
                "Check remapped_rows.pending first; a reset alone often fixes an isolated row.",
            ),
            (
                "Row remapping failure on GPU {gpu} (spare rows exhausted).",
                "GPU replaced under RMA with remapping failure and DCGM level 3 results attached.",
                "A remapping failure is an immediate RMA; no further diagnosis needed.",
            ),
        ],
        (2, 72),
    ),
    Scenario(
        "power_fault",
        "psu, node, gpu",
        "Power capping on {node} after PSU loss",
        [
            "All GPUs on {node} dropped to a {limit} W enforced limit; BMC reported {psu} input lost.",
            "Jobs on {node} slowed by {pct}%; every GPU showed sw_power_cap at a reduced limit.",
        ],
        [
            (
                "{psu} lost input: PDU outlet breaker tripped.",
                "Datacenter operations reset the breaker; limits returned to default automatically.",
                "Check the PDU before replacing a PSU; two other nodes on the same PDU were affected.",
            ),
            (
                "{psu} module failed.",
                "Hot-swapped {psu} from spares; enforced limits back to default.",
                "No drain needed for a PSU swap on redundant chassis.",
            ),
            (
                "Power cap left in place after a facility event the week before.",
                "Removed the manual cap with nvidia-smi -pl; no hardware fault.",
                "Record every manual cap with an end condition; forgotten caps look like PSU faults.",
            ),
        ],
        (0.5, 8),
    ),
    Scenario(
        "gpu_off_bus",
        "gpu, pcie, node",
        "GPU {gpu} fell off the bus on {node}",
        [
            "XID 79 on GPU {gpu}; the GPU stopped reporting and all jobs on {node} failed.",
            "BMC PCIe fatal error on GPU slot {gpu} followed by XID 79; lspci showed the device missing.",
        ],
        [
            (
                "Faulty PCIe riser for GPU {gpu}.",
                "Node rebooted, riser replaced after a second XID 79 a week later.",
                "PCIe replays had been rising on this GPU for days before the first XID 79.",
            ),
            (
                "GPU {gpu} hardware fault (serial {serial}).",
                "Rebooted; GPU failed DCGM level 3; replaced under RMA.",
                "Always run DCGM level 3 after an XID 79 recovery before resuming.",
            ),
            (
                "Driver bug in an old driver version, triggered by a specific workload.",
                "Rebooted; upgraded the driver fleet-wide after canary testing.",
                "Check the driver release notes when XID 79 appears on several nodes with healthy hardware.",
            ),
        ],
        (1, 48),
    ),
    Scenario(
        "nvlink_degradation",
        "nvlink, gpu, nvswitch",
        "NVLink errors between GPUs {pair} on {node}",
        [
            "NVLink CRC errors climbed to {crc} per minute between GPUs {pair}; all-reduce time doubled.",
            "XID 74 on GPU {first}; training throughput on {node} dropped {pct}%.",
        ],
        [
            (
                "Link training issue cleared by a baseboard reset.",
                "Drained, reset all GPUs on the baseboard; errors did not return.",
                "On NVSwitch systems, reset the whole baseboard, not one GPU.",
            ),
            (
                "Faulty NVSwitch port on the baseboard.",
                "Errors returned after reset; baseboard replaced by the vendor.",
                "Attach nvidia-smi nvlink -e counters from both GPUs to the RMA.",
            ),
        ],
        (2, 96),
    ),
    Scenario(
        "pcie_degradation",
        "pcie, gpu, node",
        "PCIe replays on {node} GPU {gpu}",
        [
            "PCIe replay counter on GPU {gpu} rising {replays} per minute; data loading slowed.",
            "GPU {gpu} trained at x8 instead of x16 after a reboot; replays climbing.",
        ],
        [
            (
                "Riser not fully seated after maintenance.",
                "Reseated the riser; link back at x16 Gen5, counter flat.",
                "Check LnkSta against LnkCap after every service visit.",
            ),
            (
                "Degraded PCIe cable to the retimer.",
                "Replaced the cable; replays stopped.",
                "Replays on one GPU only point at its own path, not the PCIe switch.",
            ),
        ],
        (1, 24),
    ),
    Scenario(
        "noisy_neighbor",
        "gpu, scheduler",
        "Suspected overheating on {node} (no fault)",
        [
            "Detector raised a temperature anomaly on GPUs {zone}; utilization went from idle to 99%.",
            "On-call paged for high power and temperature on {node}; no errors in any counter.",
        ],
        [
            (
                "A new large training job started on previously idle GPUs; temperatures matched the power draw.",
                "No action; closed as a workload change.",
                "Compare temperature with power before suspecting cooling; busy is not broken.",
            ),
            (
                "Another team's job was sharing the node and saturating the GPUs.",
                "Moved the job to exclusive nodes via the scheduler; no hardware action.",
                "Noisy neighbors are a scheduling problem, not a hardware one.",
            ),
        ],
        (0.2, 2),
    ),
]


def render(i: int, s: Scenario, rng: random.Random, start: datetime) -> tuple[str, str]:
    node = rng.choice(NODES)
    zone_start = rng.choice([0, 4])
    gpu = rng.randrange(8)
    first = rng.randrange(0, 8, 2)
    values = {
        "node": node,
        "gpu": gpu,
        "zone": f"{zone_start}-{zone_start + 3}",
        "pair": f"{first}-{first + 1}",
        "first": first,
        "fan": f"FAN{zone_start // 2 + rng.randint(1, 2)}",
        "psu": f"PSU{rng.randint(1, 4)}",
        "temp": rng.randint(86, 92),
        "resid": rng.randint(6, 14),
        "pct": rng.randint(12, 45),
        "sbe": rng.randint(20, 400),
        "limit": 420 if node.startswith("h100") else 240,
        "crc": rng.randint(60, 300),
        "replays": rng.randint(40, 300),
        "serial": f"16{rng.randint(10**10, 10**11 - 1)}",
    }
    cause, fix, lesson = (part.format(**values) for part in rng.choice(s.causes))
    symptom = rng.choice(s.symptoms).format(**values)
    opened = start + timedelta(days=rng.uniform(0, 365), hours=rng.uniform(0, 24))
    hours = rng.uniform(*s.hours)
    severity = {"gpu_off_bus": "sev1", "noisy_neighbor": "sev4", "pcie_degradation": "sev3"}.get(
        s.failure_type, rng.choice(["sev2", "sev2", "sev3"])
    )
    doc_id = f"inc-{2025_000 + i}"
    title = s.title.format(**values)
    text = f"""---
doc_id: {doc_id}
title: {title}
failure_types: {s.failure_type}
components: {s.components}
version: 1
node: {node}
opened: {opened:%Y-%m-%d}
severity: {severity}
---
# {doc_id}: {title}

## Summary
{symptom} Severity {severity}. Resolved in {hours:.1f} hours.

## Timeline
- {opened:%Y-%m-%d %H:%M} UTC: {symptom}
- {opened + timedelta(minutes=rng.randint(5, 40)):%H:%M} UTC: on-call acknowledged and started diagnosis.
- {opened + timedelta(hours=hours):%Y-%m-%d %H:%M} UTC: resolved.

## Root cause
{cause}

## Resolution
{fix}

## Lessons
{lesson}
"""
    return doc_id, text


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--count", type=int, default=64)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    rng = random.Random(args.seed)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for old in OUT_DIR.glob("inc-*.md"):
        old.unlink()
    start = datetime(2025, 1, 1, tzinfo=UTC)
    for i in range(args.count):
        doc_id, text = render(i, SCENARIOS[i % len(SCENARIOS)], rng, start)
        (OUT_DIR / f"{doc_id}.md").write_text(text)
    print(f"wrote {args.count} incident reports to {OUT_DIR}")


if __name__ == "__main__":
    main()
