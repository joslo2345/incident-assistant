"""Alert rules, in three layers that can be switched on independently (to measure each one):

1. static: fixed thresholds on metrics and counters, plus event rules for XIDs and BMC entries
2. zscore: per-GPU rolling z-scores on temperature, power and utilization
3. residual: per-GPU thermal model, temperature = a_gpu + b_model * power. A GPU running hotter
   than its power explains points at cooling; a z-score spike that the model *does* explain is a
   workload change (e.g. a noisy neighbor), so it's downgraded to informational.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from detector.features import BmcRecord, GpuMinute, XidRecord
from incident_contracts import DetectorKind, Severity

SEV1, SEV2, SEV3, SEV4 = Severity.SEV1, Severity.SEV2, Severity.SEV3, Severity.SEV4
MINUTE = timedelta(minutes=1)


@dataclass(frozen=True)
class Alert:
    # When the detector could know: the event's own time for XID/BMC entries, and the end of the
    # minute for metric-based alerts (a minute's aggregate only exists once the minute is over).
    time: datetime
    node_id: str
    gpu_index: int | None
    kind: str
    severity: Severity
    detector: DetectorKind
    signal: str
    description: str
    value: float | None = None
    threshold: float | None = None
    score: float | None = None


@dataclass(frozen=True)
class Config:
    static: bool = True
    zscore: bool = True
    residual: bool = True

    temp_high_c: float = 83.0
    sbe_window_min: int = 30
    sbe_warn: int = 5  # correctable errors per window (healthy: ~0.5/day)
    sbe_burst: int = 50
    link_window_min: int = 10
    pcie_replays: int = 100  # per link window
    nvlink_crc: int = 50
    power_limit_drop: float = 0.9  # enforced limit below 90% of the GPU's nominal limit
    missing_minutes: int = 2

    z_threshold: float = 4.0
    z_minutes: int = 3  # consecutive minutes beyond the threshold
    z_alpha: float = 1 / 60  # EWMA weight per minute (~1 h memory)
    z_warmup_min: int = 30
    std_floor: dict[str, float] = field(
        default_factory=lambda: {"temp": 2.0, "power": 35.0, "util": 5.0}
    )

    # Hotter than the thermal model predicts by this much = cooling problem. Anything below it
    # (including running cooler) means a z-spike is explained by workload, not hardware.
    residual_c: float = 5.0
    residual_minutes: int = 3
    residual_alpha: float = 1 / 360  # per-GPU intercept memory (~6 h)
    # Heatsink time constant: temperature follows power with roughly this lag, so the model
    # predicts from power filtered with it. Without this, every job that stops looks "too hot"
    # for a few minutes while the heatsink cools down.
    thermal_tau_s: float = 90.0


# Kind -> severity. Classification lives in incidents.py.
SEVERITY: dict[str, Severity] = {
    "xid_79": SEV1, "gpu_missing": SEV1, "pcie_fatal": SEV1, "ecc_dbe": SEV1, "xid_48": SEV1,
    "fan_failure": SEV2, "thermal_throttle": SEV2, "psu_fault": SEV2, "power_limit_reduced": SEV2,
    "ecc_sbe_burst": SEV2, "xid_74": SEV2, "bmc_critical": SEV2,
    "temp_high": SEV3, "thermal_residual": SEV3, "ecc_sbe": SEV3, "retired_pages": SEV3,
    "nvlink_errors": SEV3, "pcie_replays": SEV3, "xid_other": SEV3,
    "z_temp": SEV3, "z_power": SEV3, "z_util": SEV3,  # without the residual layer
    "inlet_warning": SEV4, "bmc_warning": SEV4, "workload_shift": SEV4,
}  # fmt: skip


class Ewma:
    """Exponentially weighted mean and variance."""

    def __init__(self, alpha: float) -> None:
        self.alpha = alpha
        self.mean: float | None = None
        self.var = 0.0
        self.n = 0

    def z(self, x: float, std_floor: float) -> float:
        if self.mean is None:
            return 0.0
        return (x - self.mean) / max(math.sqrt(self.var), std_floor)

    def update(self, x: float) -> None:
        self.n += 1
        if self.mean is None:
            self.mean = x
            return
        d = x - self.mean
        self.mean += self.alpha * d
        self.var = (1 - self.alpha) * (self.var + self.alpha * d * d)


class ThermalSlope:
    """Fleet-wide slope b (C per W) for one GPU model, by exponentially weighted regression."""

    def __init__(self, alpha: float = 1 / 2000) -> None:
        self.alpha = alpha
        self.n = 0
        self.mx = self.my = self.cxx = self.cxy = 0.0

    def update(self, power: float, temp: float) -> None:
        self.n += 1
        a = max(self.alpha, 1 / self.n)
        dx, dy = power - self.mx, temp - self.my
        self.mx += a * dx
        self.my += a * dy
        self.cxx = (1 - a) * (self.cxx + a * dx * dx)
        self.cxy = (1 - a) * (self.cxy + a * dx * dy)

    @property
    def slope(self) -> float | None:
        if self.n < 200 or self.cxx < 50**2:  # need enough data over a wide power range
            return None
        return self.cxy / self.cxx


@dataclass
class GpuState:
    node_id: str
    gpu_index: int
    cfg: Config
    last: GpuMinute | None = None
    missing_streak: int = 0
    nominal_limit: float = 0.0
    minutes_seen: int = 0
    sbe_deltas: deque[int] = field(default_factory=deque)
    pcie_deltas: deque[int] = field(default_factory=deque)
    nvlink_deltas: deque[int] = field(default_factory=deque)
    z: dict[str, Ewma] = field(default_factory=dict)
    z_streak: dict[str, int] = field(default_factory=dict)
    intercept: Ewma | None = None
    residual_streak: int = 0
    power_lagged: float | None = None

    def __post_init__(self) -> None:
        self.z = {k: Ewma(self.cfg.z_alpha) for k in ("temp", "power", "util")}
        self.z_streak = dict.fromkeys(self.z, 0)
        self.intercept = Ewma(self.cfg.residual_alpha)


def _delta(new: int | None, old: int | None) -> int:
    """Counter increase; a decrease means the counter reset (driver reload), not negative errors."""
    if new is None or old is None:
        return 0
    return new - old if new >= old else 0


def _window_sum(window: deque[int], value: int, size: int) -> int:
    window.append(value)
    while len(window) > size:
        window.popleft()
    return sum(window)


class RuleEngine:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.gpus: dict[tuple[str, int], GpuState] = {}
        self.slopes: dict[str, ThermalSlope] = {}

    # Events ------------------------------------------------------------------------------------

    def xid_alerts(self, x: XidRecord) -> list[Alert]:
        if not self.cfg.static:
            return []
        kind = {79: "xid_79", 48: "xid_48", 74: "xid_74"}.get(x.code, "xid_other")
        return [
            Alert(
                x.time,
                x.node_id,
                x.gpu_index,
                kind,
                SEVERITY[kind],
                DetectorKind.EVENT_MATCH,
                f"xid_{x.code}",
                f"XID {x.code}: {x.message}",
                value=x.code,
            )
        ]

    def bmc_alerts(self, b: BmcRecord) -> list[Alert]:
        if not self.cfg.static or b.severity == "ok" or b.entry_code == "deassert":
            return []
        gpu = None
        if b.sensor_type == "fan" and b.severity == "critical":
            kind = "fan_failure"
        elif b.sensor_type == "power_supply":
            kind = "psu_fault"
        elif b.sensor_type == "critical_interrupt" and b.severity == "critical":
            kind = "pcie_fatal"
            if b.sensor_name and b.sensor_name.startswith("PCIe_GPU"):
                gpu = int(b.sensor_name.removeprefix("PCIe_GPU"))
        elif b.sensor_name == "Inlet_Temp":
            kind = "inlet_warning"
        else:
            kind = "bmc_critical" if b.severity == "critical" else "bmc_warning"
        return [
            Alert(
                b.time,
                b.node_id,
                gpu,
                kind,
                SEVERITY[kind],
                DetectorKind.EVENT_MATCH,
                b.message_id,
                f"BMC {b.severity}: {b.message}",
                value=b.reading,
            )
        ]

    # Metrics -----------------------------------------------------------------------------------

    def gpu_alerts(self, m: GpuMinute) -> list[Alert]:
        key = (m.node_id, m.gpu_index)
        st = self.gpus.get(key) or self.gpus.setdefault(key, GpuState(*key, self.cfg))
        st.missing_streak = 0
        alerts: list[Alert] = []
        if self.cfg.static:
            alerts += self._static(m, st)
        explained = True
        if self.cfg.residual:
            residual_alerts, explained = self._residual(m, st)
            alerts += residual_alerts
        if self.cfg.zscore:
            alerts += self._zscore(m, st, explained)
        st.last = m
        st.minutes_seen += 1
        return alerts

    def missing_alerts(
        self, minute: datetime, reporting_nodes: set[str], seen: set[tuple[str, int]]
    ) -> list[Alert]:
        """GPUs that reported before but are silent while the rest of their node reports."""
        if not self.cfg.static:
            return []
        alerts = []
        for key, st in self.gpus.items():
            if key in seen or key[0] not in reporting_nodes:
                continue
            st.missing_streak += 1
            if st.missing_streak == self.cfg.missing_minutes:
                alerts.append(
                    Alert(
                        minute + MINUTE,
                        key[0],
                        key[1],
                        "gpu_missing",
                        SEV1,
                        DetectorKind.STATIC_THRESHOLD,
                        "samples",
                        f"GPU {key[1]} stopped reporting while the rest of the node "
                        f"reports ({st.missing_streak} min)",
                        value=0,
                        threshold=1,
                    )
                )
        return alerts

    def _static(self, m: GpuMinute, st: GpuState) -> list[Alert]:
        cfg, out = self.cfg, []

        def alert(kind: str, signal: str, desc: str, value: float, threshold: float) -> None:
            out.append(
                Alert(
                    m.minute + MINUTE,
                    m.node_id,
                    m.gpu_index,
                    kind,
                    SEVERITY[kind],
                    DetectorKind.STATIC_THRESHOLD,
                    signal,
                    desc,
                    value,
                    threshold,
                )
            )

        if m.thermal_throttled:
            alert(
                "thermal_throttle",
                "throttle_reasons",
                f"Thermal slowdown in "
                f"{m.thermal_throttled}/{m.samples} samples at {m.temp_max:.0f} C",
                m.thermal_throttled,
                1,
            )
        elif m.temp_max >= cfg.temp_high_c:
            alert(
                "temp_high",
                "temperature_c",
                f"Temperature {m.temp_max:.0f} C",
                m.temp_max,
                cfg.temp_high_c,
            )

        st.nominal_limit = max(st.nominal_limit, m.power_limit)
        if m.power_limit < cfg.power_limit_drop * st.nominal_limit:
            alert(
                "power_limit_reduced",
                "power_limit_w",
                f"Enforced power limit {m.power_limit:.0f} W, nominal {st.nominal_limit:.0f} W",
                m.power_limit,
                cfg.power_limit_drop * st.nominal_limit,
            )

        prev = st.last
        if prev is not None:
            if (d := _delta(m.dbe, prev.dbe)) > 0:
                alert("ecc_dbe", "ecc_dbe_total", f"{d} uncorrectable ECC error(s)", d, 1)
            if (d := _delta(m.retired, prev.retired)) > 0:
                alert("retired_pages", "retired_pages_total", f"{d} page(s) retired", d, 1)
            sbe = _window_sum(st.sbe_deltas, _delta(m.sbe, prev.sbe), cfg.sbe_window_min)
            if sbe >= cfg.sbe_burst:
                alert(
                    "ecc_sbe_burst",
                    "ecc_sbe_total",
                    f"{sbe} correctable ECC errors in {cfg.sbe_window_min} min",
                    sbe,
                    cfg.sbe_burst,
                )
            elif sbe >= cfg.sbe_warn:
                alert(
                    "ecc_sbe",
                    "ecc_sbe_total",
                    f"{sbe} correctable ECC errors in {cfg.sbe_window_min} min",
                    sbe,
                    cfg.sbe_warn,
                )
            pcie = _window_sum(st.pcie_deltas, _delta(m.pcie, prev.pcie), cfg.link_window_min)
            if pcie >= cfg.pcie_replays:
                alert(
                    "pcie_replays",
                    "pcie_replay_total",
                    f"{pcie} PCIe replays in {cfg.link_window_min} min",
                    pcie,
                    cfg.pcie_replays,
                )
            nvl = _window_sum(st.nvlink_deltas, _delta(m.nvlink, prev.nvlink), cfg.link_window_min)
            if nvl >= cfg.nvlink_crc:
                alert(
                    "nvlink_errors",
                    "nvlink_crc_errors_total",
                    f"{nvl} NVLink CRC errors in {cfg.link_window_min} min",
                    nvl,
                    cfg.nvlink_crc,
                )
        return out

    def _zscore(self, m: GpuMinute, st: GpuState, explained: bool) -> list[Alert]:
        cfg, out = self.cfg, []
        values = {"temp": m.temp_avg, "power": m.power_avg, "util": m.util_avg}
        for name, x in values.items():
            ewma = st.z[name]
            z = ewma.z(x, cfg.std_floor[name])
            ewma.update(x)
            if st.minutes_seen < cfg.z_warmup_min:
                continue
            st.z_streak[name] = st.z_streak[name] + 1 if abs(z) >= cfg.z_threshold else 0
            if st.z_streak[name] < cfg.z_minutes:
                continue
            if self.cfg.residual and explained:
                kind, desc = "workload_shift", f"{name} z={z:+.1f}, consistent with the GPU's power"
            else:
                kind, desc = f"z_{name}", f"{name} z={z:+.1f} vs this GPU's last hour"
            out.append(
                Alert(
                    m.minute + MINUTE,
                    m.node_id,
                    m.gpu_index,
                    kind,
                    SEVERITY[kind],
                    DetectorKind.ZSCORE,
                    name,
                    desc,
                    x,
                    None,
                    round(z, 2),
                )
            )
        return out

    def _residual(self, m: GpuMinute, st: GpuState) -> tuple[list[Alert], bool]:
        """Returns (alerts, explained): explained = temperature matches what power predicts."""
        cfg = self.cfg
        model = m.gpu_model or "unknown"
        slope = self.slopes.setdefault(model, ThermalSlope())
        k = 1 - math.exp(-60 / cfg.thermal_tau_s)
        if st.power_lagged is None:
            st.power_lagged = m.power_avg
        st.power_lagged += k * (m.power_avg - st.power_lagged)
        power = st.power_lagged
        b = slope.slope
        assert st.intercept is not None
        out: list[Alert] = []
        explained = True
        if b is not None and st.intercept.n >= 30 and st.intercept.mean is not None:
            residual = m.temp_avg - (st.intercept.mean + b * power)
            explained = residual < cfg.residual_c
            st.residual_streak = st.residual_streak + 1 if residual >= cfg.residual_c else 0
            if st.residual_streak >= cfg.residual_minutes:
                out.append(
                    Alert(
                        m.minute + MINUTE,
                        m.node_id,
                        m.gpu_index,
                        "thermal_residual",
                        SEVERITY["thermal_residual"],
                        DetectorKind.MODEL,
                        "temperature_c",
                        f"{residual:+.1f} C hotter than its power ({power:.0f} W) predicts",
                        m.temp_avg,
                        cfg.residual_c,
                        round(residual, 2),
                    )
                )
        # Learn only from healthy-looking minutes, so a fault doesn't become the new normal.
        if st.residual_streak == 0 and not m.thermal_throttled:
            slope.update(power, m.temp_avg)
            if b is not None:
                st.intercept.update(m.temp_avg - b * power)
        return out, explained
