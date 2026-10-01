import random
import statistics

import pytest

from incident_contracts import BmcEntryCode, GpuMetricsEvent, ThrottleReason
from replayer.cli import parse_duration
from replayer.fleet import PROFILES, Fleet
from replayer.synth import BmcNoise, FleetSimulator
from replayer.workload import GpuLoad, Workload

from .conftest import START


def test_gpu_load_sums_overlapping_instances() -> None:
    load = GpuLoad.from_intervals([(0, 100, 40, 2), (50, 150, 30, 1)])
    assert load.at(-1) == (0, 0)
    assert load.at(0) == (40, 2)
    assert load.at(75) == (70, 3)
    assert load.at(120) == (30, 1)
    assert load.at(150) == (0, 0)


def test_default_fleet_is_mixed(fleet: Fleet) -> None:
    models = [n.profile.model for n in fleet.nodes]
    assert len(fleet.nodes) == 8
    assert models.count(PROFILES["h100_sxm"].model) == 5
    assert models.count(PROFILES["a100_sxm"].model) == 3


def test_slice_covers_a_day_on_eight_machines(workload: Workload) -> None:
    assert workload.duration_s == 86_400
    assert {node for node, _ in workload.loads} == set(range(8))


def test_one_metric_sample_per_gpu_per_step(sim: FleetSimulator) -> None:
    _, events = next(sim.steps(10))
    metrics = [e for e in events if isinstance(e, GpuMetricsEvent)]
    assert len(metrics) == 64
    assert len({(e.node_id, e.gpu_index) for e in metrics}) == 64


def test_same_seed_gives_same_values(fleet: Fleet, workload: Workload) -> None:
    def values(seed: int) -> list[float]:
        sim = FleetSimulator(fleet, workload, start=START, seed=seed)
        return [e.power_w for _, ev in sim.steps(300) for e in ev if isinstance(e, GpuMetricsEvent)]

    assert values(1) == values(1)
    assert values(1) != values(2)


def test_metrics_are_physically_consistent(sim: FleetSimulator) -> None:
    samples = [e for _, ev in sim.steps(6 * 3600) for e in ev if isinstance(e, GpuMetricsEvent)]
    for e in samples:
        assert e.power_w <= e.power_limit_w
        assert 20 <= e.temperature_c <= 85
        assert e.memory_used_mib <= e.memory_total_mib
        if ThrottleReason.SW_POWER_CAP in e.throttle_reasons:
            assert e.utilization_pct > 80
        if e.utilization_pct == 0:
            assert e.power_w < 0.2 * e.power_limit_w

    busy = [e.temperature_c for e in samples if e.utilization_pct > 90]
    idle = [e.temperature_c for e in samples if e.utilization_pct == 0]
    assert busy and idle, "slice should have both busy and idle periods in 6h"
    assert statistics.mean(busy) > statistics.mean(idle) + 15


def test_temperature_lags_power(fleet: Fleet) -> None:
    # One GPU goes from idle to full load at t=600: temperature should rise gradually.
    workload = Workload(
        loads={(0, 0): GpuLoad.from_intervals([(600, 3600, 100, 10)])}, duration_s=3600
    )
    sim = FleetSimulator(fleet, workload, start=START, seed=0)
    temps = [
        e.temperature_c
        for _, ev in sim.steps(1200)
        for e in ev
        if isinstance(e, GpuMetricsEvent)
        and e.node_id == fleet.nodes[0].node_id
        and e.gpu_index == 0
    ]
    before, first_after, settled = temps[59], temps[61], temps[-1]
    assert first_after - before < 0.5 * (settled - before)
    assert settled - before > 30


def test_ecc_counters_never_decrease(sim: FleetSimulator) -> None:
    last: dict[tuple[str, int], int] = {}
    for _, events in sim.steps(3600):
        for e in events:
            if isinstance(e, GpuMetricsEvent):
                key = (e.node_id, e.gpu_index)
                assert e.ecc_sbe_total >= last.get(key, 0)
                last[key] = e.ecc_sbe_total


def test_bmc_warnings_assert_then_clear() -> None:
    noise = BmcNoise("h100-node-01", random.Random(3))
    entries = [e for t in range(0, 30 * 86_400, 10) for e in noise.sample(t, 10, START)]
    assert 10 <= len(entries) <= 120, "about one warning per node per day, plus its clear"
    codes = [e.entry_code for e in entries]
    # Alternating assert/deassert, starting with assert.
    assert codes == [BmcEntryCode.ASSERT, BmcEntryCode.DEASSERT] * (len(codes) // 2) + (
        [BmcEntryCode.ASSERT] if len(codes) % 2 else []
    )
    assert all(e.severity == "warning" for e in entries if e.entry_code == BmcEntryCode.ASSERT)


@pytest.mark.parametrize(
    ("text", "seconds"), [("90", 90), ("90s", 90), ("10m", 600), ("24h", 86_400), ("1.5h", 5400)]
)
def test_parse_duration(text: str, seconds: float) -> None:
    assert parse_duration(text) == seconds
