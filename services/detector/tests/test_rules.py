import math
import random
from datetime import timedelta

from detector_testkit import NODE, batch, bmc, gpu, minute

from detector.engine import Detector
from detector.features import XidRecord
from detector.rules import Config, RuleEngine

STATIC = Config(static=True, zscore=False, residual=False)


def kinds(alerts: list) -> set[str]:  # type: ignore[type-arg]
    return {a.kind for a in alerts}


def run(engine: RuleEngine, rows: list) -> list:  # type: ignore[type-arg]
    out = []
    for r in rows:
        out += engine.gpu_alerts(r)
    return out


def test_xid_and_bmc_event_rules() -> None:
    e = RuleEngine(STATIC)
    assert kinds(e.xid_alerts(XidRecord(minute(0), NODE, 3, 79, "off bus"))) == {"xid_79"}
    assert kinds(e.xid_alerts(XidRecord(minute(0), NODE, 3, 13, "graphics"))) == {"xid_other"}
    assert kinds(e.bmc_alerts(bmc(0, sensor_type="fan", sensor_name="FAN3"))) == {"fan_failure"}
    assert kinds(e.bmc_alerts(bmc(0, severity="warning", sensor_type="power_supply"))) == {
        "psu_fault"
    }
    pcie = e.bmc_alerts(bmc(0, sensor_type="critical_interrupt", sensor_name="PCIe_GPU5"))
    assert kinds(pcie) == {"pcie_fatal"} and pcie[0].gpu_index == 5
    inlet = bmc(0, severity="warning", sensor_type="temperature", sensor_name="Inlet_Temp")
    assert kinds(e.bmc_alerts(inlet)) == {"inlet_warning"}
    assert e.bmc_alerts(bmc(0, sensor_type="fan", entry_code="deassert")) == []


def test_counter_rules_use_increases_and_ignore_resets() -> None:
    e = RuleEngine(STATIC)
    rows = [gpu(0, sbe=10, pcie=0, nvlink=0)]
    rows += [gpu(i, sbe=10 + i, pcie=20 * i, nvlink=10 * i) for i in range(1, 8)]
    got = kinds(run(e, rows))
    assert {"ecc_sbe", "pcie_replays", "nvlink_errors"} <= got
    e2 = RuleEngine(STATIC)
    assert run(e2, [gpu(0, sbe=500), gpu(1, sbe=0), gpu(2, sbe=0)]) == [], "reset is not errors"


def test_dbe_retired_thermal_and_power_limit() -> None:
    e = RuleEngine(STATIC)
    got = kinds(run(e, [
        gpu(0), gpu(1, dbe=1, retired=2, thermal_throttled=3, temp_max=88, power_limit=420),
    ]))  # fmt: skip
    assert {"ecc_dbe", "retired_pages", "thermal_throttle", "power_limit_reduced"} <= got


def test_missing_gpu_only_when_rest_of_node_reports() -> None:
    e = RuleEngine(STATIC)
    e.gpu_alerts(gpu(0, g=0))
    e.gpu_alerts(gpu(0, g=1))
    assert e.missing_alerts(minute(1), {NODE}, {(NODE, 0)}) == []
    alerts = e.missing_alerts(minute(2), {NODE}, {(NODE, 0)})
    assert kinds(alerts) == {"gpu_missing"} and alerts[0].gpu_index == 1
    # Whole node silent (e.g. replay ended): no per-GPU alerts.
    assert e.missing_alerts(minute(3), set(), set()) == []


def physical(
    powers: list[float],
    g: int = 0,
    start: int = 0,
    extra_c: float = 0.0,
    temp0: float | None = None,
) -> list:  # type: ignore[type-arg]
    """Minute rows whose temperature follows power with a ~90 s lag, like real GPUs."""
    k = 1 - math.exp(-60 / 90)
    temp = temp0 if temp0 is not None else 24 + 0.064 * powers[0]
    rows = []
    for i, p in enumerate(powers):
        temp += k * (24 + 0.064 * p - temp)
        rows.append(gpu(start + i, g=g, power_avg=p, temp_avg=temp + extra_c,
                        temp_max=temp + extra_c + 1, util_avg=min(100.0, p / 7)))  # fmt: skip
    return rows


def varied(n: int, seed: int = 0) -> list[float]:
    rng = random.Random(seed)
    return [rng.choice([80.0, 300.0, 500.0, 690.0]) for _ in range(n) for _ in range(5)][:n]


def test_residual_flags_a_gpu_hotter_than_its_power_explains() -> None:
    engine = RuleEngine(Config(static=False, zscore=True, residual=True))
    rows = physical(varied(300))
    rows += physical([600.0] * 8, start=300, extra_c=9, temp0=rows[-1].temp_avg)
    assert "thermal_residual" in kinds(run(engine, rows))


def test_workload_jump_is_explained_not_a_thermal_alarm() -> None:
    engine = RuleEngine(Config(static=False, zscore=True, residual=True))
    run(engine, physical(varied(300), g=1))  # another GPU of the model teaches the slope
    rows = physical([70.0] * 120, g=2)
    rows += physical([690.0] * 10, g=2, start=120, temp0=rows[-1].temp_avg)
    got = kinds(run(engine, rows))
    assert "workload_shift" in got
    assert not got & {"thermal_residual", "z_temp", "z_power", "z_util"}


def test_without_residual_layer_z_spikes_are_actionable() -> None:
    engine = RuleEngine(Config(static=False, zscore=True, residual=False))
    rows = [gpu(i, power_avg=70, temp_avg=28.5, util_avg=0) for i in range(60)]
    rows += [gpu(60 + i, power_avg=690, temp_avg=68, util_avg=99) for i in range(5)]
    assert {"z_temp", "z_power", "z_util"} <= kinds(run(engine, rows))


def test_detector_groups_a_fault_into_one_classified_incident() -> None:
    d = Detector(STATIC, "t")
    d.process(batch(0, [gpu(0)]))
    changed = d.process(batch(1, [gpu(1, thermal_throttled=4, temp_max=88)],
                              bmc=[bmc(1, sensor_type="fan", sensor_name="FAN2")]))  # fmt: skip
    changed += d.process(batch(5, [gpu(5, thermal_throttled=6, temp_max=89)]))
    ids = {i.incident_id for i in changed}
    assert len(ids) == 1
    inc = changed[-1]
    assert inc.failure_type.value == "thermal_runaway" and inc.severity.value == "sev2"
    contract = inc.to_contract()
    assert contract.suspected_failure_type.value == "thermal_runaway"
    assert contract.evidence and contract.components[0].kind.value == "node"


def test_unrelated_faults_on_different_gpus_of_a_node_stay_separate() -> None:
    d = Detector(STATIC, "t")
    rows = [gpu(0, g=0, nvlink=0), gpu(0, g=4, pcie=0)]
    d.process(batch(0, rows))
    changed = []
    for i in range(1, 6):  # NVLink errors on GPU 0, then PCIe replays on GPU 4
        changed += d.process(batch(i, [gpu(i, g=0, nvlink=30 * i), gpu(i, g=4, pcie=0)]))
    for i in range(6, 12):
        changed += d.process(batch(i, [gpu(i, g=0, nvlink=150), gpu(i, g=4, pcie=40 * (i - 5))]))
    types = {inc.incident_id: inc.failure_type.value for inc in changed}
    assert sorted(types.values()) == ["nvlink_degradation", "pcie_degradation"]


def test_same_failure_type_on_other_gpus_joins_one_incident() -> None:
    d = Detector(STATIC, "t")
    d.process(batch(0, [gpu(0, g=g) for g in range(4)]))
    changed = d.process(batch(1, [gpu(1, g=g, thermal_throttled=3, temp_max=88) for g in range(4)]))
    assert len({inc.incident_id for inc in changed}) == 1
    assert changed[0].gpus == [0, 1, 2, 3]


def test_incident_opened_by_an_alert_inside_the_minute_is_valid() -> None:
    # A BMC entry 26 s into minute 1 opens the incident while minute 1 (:00) is processed; the
    # contract rejected updated_at before created_at and crashed the detector.
    d = Detector(STATIC, "t")
    d.process(batch(0, [gpu(0)]))
    t = minute(1) + timedelta(seconds=26)
    changed = d.process(
        batch(1, [gpu(1)], bmc=[bmc(1, time=t, sensor_type="fan", sensor_name="FAN2")])
    )
    contract = changed[-1].to_contract()
    assert contract.created_at == t
    assert contract.updated_at >= contract.created_at
