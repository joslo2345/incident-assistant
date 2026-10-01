import json
import random
from pathlib import Path

import pytest

from incident_contracts import BmcLogEvent, FailureType, GpuMetricsEvent, XidEvent
from replayer.faults import FAULT_TYPES, GAP_S, WARMUP_S, Fault, plan_faults, write_ground_truth
from replayer.fleet import Fleet
from replayer.synth import FleetSimulator
from replayer.workload import GpuLoad, Workload

from .conftest import START

NODES = [(f"n{i}", 8) for i in range(8)]
NODE = "h100-node-01"


def busy_workload() -> Workload:
    """Every GPU of node 0 at 100% all day, so load-dependent signatures are visible."""
    loads = {(0, g): GpuLoad.from_intervals([(0, 86_400, 100, 20)]) for g in range(8)}
    return Workload(loads=loads, duration_s=86_400)


def simulate(fleet: Fleet, fault: Fault, seconds: float) -> list[object]:
    sim = FleetSimulator(fleet, busy_workload(), start=START, seed=1, faults=[fault])
    return [e for _, events in sim.steps(seconds) for e in events]


def make(ftype: FailureType, start: float = 3600, length: float | None = None) -> Fault:
    cls = FAULT_TYPES[ftype]
    length = length or cls.duration_range_s[0]
    gpus = cls.pick_gpus(random.Random(0), 8)
    return cls(f"t-{ftype.value}", NODE, gpus, start, start + length, random.Random(0))


def gpu_samples(events: list[object], gpu: int, t0: float, t1: float) -> list[GpuMetricsEvent]:
    lo, hi = START.timestamp() + t0, START.timestamp() + t1
    return [
        e
        for e in events
        if isinstance(e, GpuMetricsEvent)
        and e.node_id == NODE
        and e.gpu_index == gpu
        and lo <= e.timestamp.timestamp() < hi
    ]


def test_plan_is_balanced_reproducible_and_non_overlapping() -> None:
    types = list(FAULT_TYPES)
    faults = plan_faults(NODES, 86_400, 14, seed=3, types=types)
    assert [f.fault_id for f in faults] == [
        f.fault_id for f in plan_faults(NODES, 86_400, 14, 3, types)
    ]
    assert sorted(f.type for f in faults) == sorted(types * 2)
    assert all(f.start_s >= WARMUP_S for f in faults)
    for a in faults:
        for b in faults:
            if a is not b and a.node_id == b.node_id:
                assert a.end_s + GAP_S <= b.start_s or b.end_s + GAP_S <= a.start_s


def test_plan_fails_loudly_when_faults_do_not_fit() -> None:
    with pytest.raises(ValueError, match="couldn't fit"):
        plan_faults(NODES[:1], 2 * 3600, 10, seed=0)


def test_ground_truth_file(tmp_path: Path) -> None:
    faults = plan_faults(NODES, 86_400, 7, seed=1)
    path = tmp_path / "gt.jsonl"
    write_ground_truth(path, faults, START)
    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(records) == 7
    decoy = next(r for r in records if r["type"] == "noisy_neighbor")
    assert decoy["is_fault"] is False
    assert all(r["start"] < r["end"] for r in records)


def test_thermal_runaway_signature(fleet: Fleet) -> None:
    fault = make(FailureType.THERMAL_RUNAWAY, length=1800)
    events = simulate(fleet, fault, 3600 + 1800)
    before = gpu_samples(events, fault.gpus[0], 2400, 3600)
    late = gpu_samples(events, fault.gpus[0], 3600 + 900, 3600 + 1800)
    assert max(e.temperature_c for e in late) > max(e.temperature_c for e in before) + 12
    assert any("hw_thermal_slowdown" in e.throttle_reasons for e in late)
    fan = [e for e in events if isinstance(e, BmcLogEvent) and e.sensor_type == "fan"]
    assert fan[0].severity == "critical" and fan[0].reading == 0


def test_ecc_degradation_signature(fleet: Fleet) -> None:
    fault = make(FailureType.ECC_DEGRADATION)
    events = simulate(fleet, fault, fault.end_s + 60)
    samples = gpu_samples(events, fault.gpus[0], 0, fault.end_s + 60)
    assert samples[-1].ecc_sbe_total - samples[0].ecc_sbe_total > 50
    assert samples[-1].ecc_dbe_total == 1 and (samples[-1].retired_pages_total or 0) > 0
    assert [e.xid_code for e in events if isinstance(e, XidEvent)] == [48]


def test_power_fault_signature(fleet: Fleet) -> None:
    fault = make(FailureType.POWER_FAULT)
    events = simulate(fleet, fault, 3600 + 1800)
    during = gpu_samples(events, 5, 3600, 3600 + 1800)
    assert all(e.power_limit_w == pytest.approx(420) for e in during)
    assert max(e.power_w for e in during) <= 420


def test_gpu_off_bus_signature(fleet: Fleet) -> None:
    fault = make(FailureType.GPU_OFF_BUS)
    events = simulate(fleet, fault, fault.end_s + 600)
    assert gpu_samples(events, fault.gpus[0], fault.start_s, fault.end_s) == []
    assert gpu_samples(events, fault.gpus[0], fault.end_s, fault.end_s + 600), "back after reboot"
    assert [e.xid_code for e in events if isinstance(e, XidEvent)] == [79]


@pytest.mark.parametrize(
    ("ftype", "counter"),
    [
        (FailureType.NVLINK_DEGRADATION, "nvlink_crc_errors_total"),
        (FailureType.PCIE_DEGRADATION, "pcie_replay_total"),
    ],
)
def test_link_degradation_signatures(fleet: Fleet, ftype: FailureType, counter: str) -> None:
    fault = make(ftype)
    events = simulate(fleet, fault, fault.end_s)
    during = gpu_samples(events, fault.gpus[0], fault.start_s, fault.end_s)
    before = gpu_samples(events, fault.gpus[0], 2400, 3600)
    assert getattr(during[-1], counter) - getattr(during[0], counter) > 1000
    util = sum(e.utilization_pct for e in during) / len(during)
    assert util < 0.95 * sum(e.utilization_pct for e in before) / len(before)


def test_noisy_neighbor_has_no_error_signals(fleet: Fleet) -> None:
    fault = make(FailureType.NOISY_NEIGHBOR)
    events = simulate(fleet, fault, fault.end_s)
    during = gpu_samples(events, fault.gpus[0], fault.start_s, fault.end_s)
    assert min(e.utilization_pct for e in during) > 95
    assert not any(isinstance(e, XidEvent) for e in events)
    assert len({e.ecc_dbe_total for e in during}) == 1


def test_dropped_bmc_entries_are_not_emitted_but_xids_are(fleet: Fleet) -> None:
    fault = make(FailureType.GPU_OFF_BUS)
    fault.bmc_dropped = True
    events = simulate(fleet, fault, fault.start_s + 60)
    assert [e.xid_code for e in events if isinstance(e, XidEvent)] == [79]
    assert not [e for e in events if isinstance(e, BmcLogEvent) and e.severity == "critical"]
