"""Fault injection with ground truth.

Each fault changes the simulator's physics or emits the events real hardware would (XID errors,
BMC entries), so detectors see realistic signatures rather than pasted-in anomalies:

- thermal_runaway (4-GPU zone): BMC fan critical at 0 RPM; cooling degrades over ~8 min;
  busy GPUs reach thermal slowdown.
- ecc_degradation (1 GPU): correctable ECC rate grows exponentially for hours, pages retire,
  then an uncorrectable error with XID 48.
- power_fault (node): BMC "PSU input lost"; the enforced power cap drops to 60% on every GPU.
- gpu_off_bus (1 GPU): XID 79 and a BMC PCIe fatal error, then no samples until the reboot.
- nvlink_degradation (GPU pair): NVLink CRC errors ramp up, XID 74, both GPUs ~25% slower.
- pcie_degradation (1 GPU): a steady stream of PCIe replays, ~10% slower.
- noisy_neighbor (2-4 GPUs): decoy. A heavy job pins utilization; power and temperature rise,
  but there are no errors.
"""

import json
import random
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar

from incident_contracts import (
    BmcEntryCode,
    BmcEntryType,
    BmcLogEvent,
    BmcSensorType,
    BmcSeverity,
    BmcSourceFormat,
    FailureType,
    TelemetryEvent,
    XidEvent,
)
from replayer.synth import GpuEffects

WARMUP_S = 45 * 60  # leave time for per-GPU baselines before the first fault
GAP_S = 15 * 60  # minimum quiet time between faults on the same node


@dataclass
class Fault:
    fault_id: str
    node_id: str
    gpus: tuple[int, ...]
    start_s: float
    end_s: float
    rng: random.Random = field(repr=False)
    # BMC logs travel a separate collection path and are often missing in practice. When set, this
    # fault's BMC entries are never emitted (driver XIDs still are).
    bmc_dropped: bool = False

    type: ClassVar[FailureType]
    is_fault: ClassVar[bool] = True
    scope: ClassVar[str]  # what ground truth points at: "gpu" or "node"
    duration_range_s: ClassVar[tuple[float, float]]

    def __post_init__(self) -> None:
        """Per-fault setup in subclasses (dataclasses only call it if the base defines it)."""

    def active(self, t: float) -> bool:
        return self.start_s <= t < self.end_s

    def progress(self, t: float) -> float:
        return min(1.0, max(0.0, (t - self.start_s) / (self.end_s - self.start_s)))

    def apply(self, gpu: int, t: float, fx: GpuEffects) -> None:
        """Modify the effects for `gpu` at time t (only called while active and gpu is affected)."""

    def events(self, t: float, dt: float, at: datetime) -> list[TelemetryEvent]:
        """One-shot events (XID, BMC) due at sample t, minus BMC entries if dropped."""
        events = self._events(t, dt, at)
        if self.bmc_dropped:
            events = [e for e in events if not isinstance(e, BmcLogEvent)]
        return events

    def _events(self, t: float, dt: float, at: datetime) -> list[TelemetryEvent]:
        return []

    @staticmethod
    def due(t: float, dt: float, mark: float) -> bool:
        """True for the first sample at or after `mark`, so events never predate the fault."""
        return t - dt < mark <= t

    @classmethod
    def pick_gpus(cls, rng: random.Random, n_gpus: int) -> tuple[int, ...]:
        return (rng.randrange(n_gpus),)

    def describe(self) -> str:
        return self.__doc__.strip().splitlines()[0] if self.__doc__ else self.type.value

    def ground_truth(self, start: datetime) -> dict[str, Any]:
        return {
            "fault_id": self.fault_id,
            "type": self.type.value,
            "is_fault": self.is_fault,
            "scope": self.scope,
            "node_id": self.node_id,
            "gpu_indices": list(self.gpus),
            "start": (start + timedelta(seconds=self.start_s)).isoformat(),
            "end": (start + timedelta(seconds=self.end_s)).isoformat(),
            "description": self.describe(),
            "bmc_dropped": self.bmc_dropped,
        }

    # Event builders shared by subclasses.
    def _xid(self, at: datetime, gpu: int, code: int, message: str) -> XidEvent:
        return XidEvent(
            event_id=uuid.uuid4(),
            timestamp=at,
            node_id=self.node_id,
            gpu_index=gpu,
            xid_code=code,
            message=message,
        )

    def _bmc(self, at: datetime, **fields: Any) -> BmcLogEvent:
        return BmcLogEvent(
            event_id=uuid.uuid4(),
            timestamp=at,
            node_id=self.node_id,
            source_format=BmcSourceFormat.REDFISH,
            entry_id=str(self.rng.randint(1000, 9999)),
            **fields,
        )


class ThermalRunaway(Fault):
    """Fan failure in a GPU cooling zone; GPUs overheat and thermal-throttle."""

    type = FailureType.THERMAL_RUNAWAY
    scope = "node"
    duration_range_s = (20 * 60, 40 * 60)

    def __post_init__(self) -> None:
        self.fan = f"FAN{(self.gpus[0] // 4) * 2 + self.rng.randint(1, 2)}"

    @classmethod
    def pick_gpus(cls, rng: random.Random, n_gpus: int) -> tuple[int, ...]:
        zone = rng.randrange(max(1, n_gpus // 4))
        return tuple(range(zone * 4, min(n_gpus, zone * 4 + 4)))

    def apply(self, gpu: int, t: float, fx: GpuEffects) -> None:
        # The fan spins down and heat soaks the heatsinks over ~8 minutes.
        fx.cooling_factor *= 1 + 0.7 * min(1.0, (t - self.start_s) / 480)

    def _events(self, t: float, dt: float, at: datetime) -> list[TelemetryEvent]:
        def fan(code: BmcEntryCode, rpm: float) -> BmcLogEvent:
            low = code == BmcEntryCode.ASSERT
            return self._bmc(
                at,
                entry_type=BmcEntryType.SEL,
                severity=BmcSeverity.CRITICAL if low else BmcSeverity.OK,
                message_id="OpenBMC.0.1.SensorThresholdCriticalLowGoingLow"
                if low
                else "OpenBMC.0.1.SensorThresholdCriticalLowGoingHigh",
                message=f"{self.fan} sensor crossed a critical low threshold going "
                f"{'low' if low else 'high'}. Reading={rpm:.0f} Threshold=500.",
                message_args=[self.fan, f"{rpm:.0f}", "500"],
                sensor_type=BmcSensorType.FAN,
                sensor_number=40 + int(self.fan[3:]),
                sensor_name=self.fan,
                entry_code=code,
                reading=rpm,
                unit="RPM",
            )

        if self.due(t, dt, self.start_s):
            return [fan(BmcEntryCode.ASSERT, 0)]
        if self.due(t, dt, self.end_s):  # fan replaced
            return [fan(BmcEntryCode.DEASSERT, self.rng.uniform(9000, 11000))]
        return []


class EccDegradation(Fault):
    """Degrading HBM: correctable ECC errors accelerate until an uncorrectable error."""

    type = FailureType.ECC_DEGRADATION
    scope = "gpu"
    duration_range_s = (2 * 3600, 6 * 3600)

    def __post_init__(self) -> None:
        self._dbe_pending = False

    def apply(self, gpu: int, t: float, fx: GpuEffects) -> None:
        fx.sbe_per_hour += 2 * 200 ** self.progress(t)  # 2/h growing to 400/h
        if self._dbe_pending:
            fx.dbe += 1
            self._dbe_pending = False

    def _events(self, t: float, dt: float, at: datetime) -> list[TelemetryEvent]:
        # The uncorrectable error lands in the last interval; events run before samples, so the
        # same interval's metrics already show ecc_dbe_total + 1.
        if self.due(t, dt, self.end_s - dt) and t < self.end_s:
            self._dbe_pending = True
            return [self._xid(at, self.gpus[0], 48, "DBE (Double Bit Error) ECC Error.")]
        return []


class PowerFault(Fault):
    """PSU input lost; the node caps every GPU's power."""

    type = FailureType.POWER_FAULT
    scope = "node"
    duration_range_s = (30 * 60, 90 * 60)

    @classmethod
    def pick_gpus(cls, rng: random.Random, n_gpus: int) -> tuple[int, ...]:
        return tuple(range(n_gpus))

    def __post_init__(self) -> None:
        self.psu = f"PSU{self.rng.randint(1, 4)}"

    def apply(self, gpu: int, t: float, fx: GpuEffects) -> None:
        fx.power_limit_factor = min(fx.power_limit_factor, 0.6)

    def _events(self, t: float, dt: float, at: datetime) -> list[TelemetryEvent]:
        if self.due(t, dt, self.start_s):
            return [
                self._bmc(
                    at,
                    entry_type=BmcEntryType.EVENT,
                    severity=BmcSeverity.WARNING,
                    message_id="ResourceEvent.1.3.ResourceErrorsDetected",
                    message=f"The resource property {self.psu} has detected errors of type "
                    "'Input lost'.",
                    message_args=[self.psu, "Input lost"],
                    sensor_type=BmcSensorType.POWER_SUPPLY,
                    sensor_name=self.psu,
                )
            ]
        if self.due(t, dt, self.end_s):
            return [
                self._bmc(
                    at,
                    entry_type=BmcEntryType.EVENT,
                    severity=BmcSeverity.OK,
                    message_id="ResourceEvent.1.3.ResourceErrorsCorrected",
                    message=f"The resource property {self.psu} has corrected errors of type "
                    "'Input lost'.",
                    message_args=[self.psu, "Input lost"],
                    sensor_type=BmcSensorType.POWER_SUPPLY,
                    sensor_name=self.psu,
                )
            ]
        return []


class GpuOffBus(Fault):
    """GPU fell off the PCIe bus (XID 79) and stops reporting until the node reboots."""

    type = FailureType.GPU_OFF_BUS
    scope = "gpu"
    duration_range_s = (30 * 60, 60 * 60)

    def apply(self, gpu: int, t: float, fx: GpuEffects) -> None:
        fx.offline = True

    def _events(self, t: float, dt: float, at: datetime) -> list[TelemetryEvent]:
        if not self.due(t, dt, self.start_s):
            return []
        return [
            self._xid(at, self.gpus[0], 79, "GPU has fallen off the bus."),
            self._bmc(
                at,
                entry_type=BmcEntryType.SEL,
                severity=BmcSeverity.CRITICAL,
                message_id="OpenBMC.0.1.PCIeFatalError",
                message=f"PCIe fatal error on GPU slot {self.gpus[0]}.",
                message_args=[f"GPU{self.gpus[0]}"],
                sensor_type=BmcSensorType.CRITICAL_INTERRUPT,
                sensor_number=60 + self.gpus[0],
                sensor_name=f"PCIe_GPU{self.gpus[0]}",
                entry_code=BmcEntryCode.ASSERT,
            ),
        ]


class NvlinkDegradation(Fault):
    """NVLink between two GPUs degrades: CRC errors ramp up and training slows down."""

    type = FailureType.NVLINK_DEGRADATION
    scope = "gpu"
    duration_range_s = (30 * 60, 90 * 60)

    @classmethod
    def pick_gpus(cls, rng: random.Random, n_gpus: int) -> tuple[int, ...]:
        g = rng.randrange(0, n_gpus - 1, 2)
        return (g, g + 1)

    def apply(self, gpu: int, t: float, fx: GpuEffects) -> None:
        fx.nvlink_crc_per_min += 5 + 295 * self.progress(t)
        fx.util_factor *= 0.75

    def _events(self, t: float, dt: float, at: datetime) -> list[TelemetryEvent]:
        mark = self.start_s + 0.4 * (self.end_s - self.start_s)
        if self.due(t, dt, mark):
            return [self._xid(at, self.gpus[0], 74, "NVLINK Error. Link 3 CRC errors.")]
        return []


class PcieDegradation(Fault):
    """Marginal PCIe link: a steady stream of replays and a small slowdown."""

    type = FailureType.PCIE_DEGRADATION
    scope = "gpu"
    duration_range_s = (30 * 60, 90 * 60)

    def __post_init__(self) -> None:
        self.rate = self.rng.uniform(50, 300)

    def apply(self, gpu: int, t: float, fx: GpuEffects) -> None:
        fx.pcie_replays_per_min += self.rate
        fx.util_factor *= 0.9


class NoisyNeighbor(Fault):
    """Decoy: a heavy job pins several GPUs at 100%. Looks alarming, isn't a fault."""

    type = FailureType.NOISY_NEIGHBOR
    is_fault = False
    scope = "gpu"
    duration_range_s = (20 * 60, 60 * 60)

    @classmethod
    def pick_gpus(cls, rng: random.Random, n_gpus: int) -> tuple[int, ...]:
        return tuple(sorted(rng.sample(range(n_gpus), rng.randint(2, 4))))

    def apply(self, gpu: int, t: float, fx: GpuEffects) -> None:
        fx.util_floor = max(fx.util_floor, 98.0)


FAULT_TYPES: dict[FailureType, type[Fault]] = {
    cls.type: cls
    for cls in (
        ThermalRunaway,
        EccDegradation,
        PowerFault,
        GpuOffBus,
        NvlinkDegradation,
        PcieDegradation,
        NoisyNeighbor,
    )
}


def plan_faults(
    nodes: Sequence[tuple[str, int]],
    duration_s: float,
    count: int,
    seed: int,
    types: Sequence[FailureType] = tuple(FAULT_TYPES),
    bmc_dropout: float = 0.0,
) -> list[Fault]:
    """Place `count` faults, cycling through `types`, on random nodes without overlaps per node.

    nodes: (node_id, gpu count) pairs. Raises if the faults can't all fit.
    """
    rng = random.Random(seed)
    order = [types[i % len(types)] for i in range(count)]
    rng.shuffle(order)
    busy: dict[str, list[tuple[float, float]]] = {n: [] for n, _ in nodes}
    faults: list[Fault] = []
    for i, ftype in enumerate(order):
        cls = FAULT_TYPES[ftype]
        for _ in range(500):
            node_id, n_gpus = rng.choice(list(nodes))
            length = rng.uniform(*cls.duration_range_s)
            latest = duration_s - length - GAP_S
            if latest <= WARMUP_S:
                continue
            start = rng.uniform(WARMUP_S, latest)
            end = start + length
            if all(end + GAP_S <= s or start >= e + GAP_S for s, e in busy[node_id]):
                break
        else:
            raise ValueError(
                f"couldn't fit {count} faults into {duration_s / 3600:.1f} h; "
                "use a longer duration or fewer faults"
            )
        busy[node_id].append((start, end))
        fault_rng = random.Random(rng.getrandbits(64))
        faults.append(
            cls(
                fault_id=f"f{i:03d}-{ftype.value}",
                node_id=node_id,
                gpus=cls.pick_gpus(fault_rng, n_gpus),
                start_s=round(start),
                end_s=round(end),
                rng=fault_rng,
                bmc_dropped=rng.random() < bmc_dropout,
            )
        )
    return sorted(faults, key=lambda f: f.start_s)


def write_ground_truth(path: Path, faults: Sequence[Fault], start: datetime) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for fault in faults:
            f.write(json.dumps(fault.ground_truth(start)) + "\n")
