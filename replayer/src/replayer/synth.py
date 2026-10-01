"""Turns per-GPU load into DCGM-style metrics and BMC log entries.

The model is simple but physically consistent, so injected faults in A3 stand out the way they
would on real hardware:
- utilization: load from the trace plus slow random wander (jobs are not perfectly steady)
- power: idle + (limit - idle) * util^0.9, capped at the limit (sets the power-cap throttle flag)
- temperature: moves toward inlet + k * power with a first-order lag, so it trails power
- clocks: max boost under load, lower when power-capped, idle clock when idle
- error counters: correctable ECC errors arrive rarely at random; everything else stays at zero
"""

import math
import random
import uuid
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from incident_contracts import (
    BmcEntryCode,
    BmcEntryType,
    BmcLogEvent,
    BmcSensorType,
    BmcSeverity,
    BmcSourceFormat,
    GpuMetricsEvent,
    TelemetryEvent,
    ThrottleReason,
)
from replayer.fleet import Fleet, GpuProfile
from replayer.workload import GpuLoad, Workload

if TYPE_CHECKING:
    from replayer.faults import Fault

SBE_PER_GPU_PER_DAY = 0.5  # healthy HBM sees the occasional corrected error
INLET_WARNINGS_PER_NODE_PER_DAY = 1.0  # benign BMC noise: brief inlet-temp warnings
DRIVER_OVERHEAD_MIB = 512


@dataclass
class GpuEffects:
    """How active faults change one GPU for one sample. The defaults mean healthy."""

    cooling_factor: float = 1.0  # >1: worse cooling (multiplies degrees per watt)
    power_limit_factor: float = 1.0  # <1: enforced power cap below the GPU's nominal limit
    util_factor: float = 1.0  # <1: work slowed down (e.g. link retries)
    util_floor: float = 0.0  # noisy neighbor: someone else's job keeps the GPU busy
    sbe_per_hour: float = 0.0  # extra correctable ECC errors
    pcie_replays_per_min: float = 0.0
    nvlink_crc_per_min: float = 0.0
    offline: bool = False  # fell off the bus: no samples at all
    dbe: int = 0  # uncorrectable ECC errors to add now (one-shot)


HEALTHY = GpuEffects()


def poisson(rng: random.Random, lam: float) -> int:
    """Knuth's algorithm; fine for the small rates used per sample."""
    if lam <= 0:
        return 0
    if lam > 30:  # normal approximation keeps big bursts cheap
        return max(0, round(rng.gauss(lam, math.sqrt(lam))))
    limit, k, prod = math.exp(-lam), 0, rng.random()
    while prod > limit:
        k += 1
        prod *= rng.random()
    return k


@dataclass
class GpuSimulator:
    node_id: str
    gpu_index: int
    profile: GpuProfile
    load: GpuLoad
    inlet_c: float
    rng: random.Random

    def __post_init__(self) -> None:
        self.gpu_uuid = f"GPU-{uuid.UUID(int=self.rng.getrandbits(128), version=4)}"
        self._wander = 0.0  # slow multiplicative noise on utilization
        self._temp_c: float | None = None
        self._thermal_throttled = False
        self._sbe = self.rng.randint(0, 3)
        self._dbe = 0
        self._sbe_since_retire = 0
        self._retired = 0
        self._pcie_replay = self.rng.randint(0, 2)
        self._nvlink_crc = 0

    def sample(
        self, t_s: float, dt_s: float, at: datetime, fx: GpuEffects = HEALTHY
    ) -> GpuMetricsEvent | None:
        p, rng = self.profile, self.rng
        load_util, load_mem_gib = self.load.at(t_s)

        # AR(1) wander so a busy GPU drifts between ~90% and 100% rather than sitting flat.
        self._wander = 0.9 * self._wander + rng.gauss(0, 0.02)
        util = min(100.0, load_util * (1 + self._wander)) if load_util > 0 else 0.0
        util = max(fx.util_floor * (1 + self._wander / 4), util * fx.util_factor)
        util = min(100.0, max(0.0, util))

        limit = p.power_limit_w * fx.power_limit_factor
        power = p.power_idle_w + (p.power_limit_w - p.power_idle_w) * (util / 100) ** 0.9
        if self._thermal_throttled:
            power *= 0.7  # lower clocks at the same utilization draw less power
        power *= 1 + rng.gauss(0, 0.015)
        throttle: list[ThrottleReason] = []
        if power >= 0.98 * limit:
            power = limit * rng.uniform(0.97, 1.0)
            throttle.append(ThrottleReason.SW_POWER_CAP)

        target_c = self.inlet_c + p.thermal_c_per_w * fx.cooling_factor * power
        if self._temp_c is None:
            self._temp_c = target_c
        self._temp_c += (target_c - self._temp_c) * (1 - math.exp(-dt_s / p.thermal_tau_s))
        temp_c = self._temp_c + rng.gauss(0, 0.3)

        # Thermal slowdown with 3 C of hysteresis, applied to this sample's clocks and the next
        # sample's power (the GPU reacts within its sampling interval).
        if temp_c >= p.slowdown_temp_c:
            self._thermal_throttled = True
        elif temp_c < p.slowdown_temp_c - 3:
            self._thermal_throttled = False
        if self._thermal_throttled:
            throttle += [ThrottleReason.HW_THERMAL_SLOWDOWN, ThrottleReason.SW_THERMAL_SLOWDOWN]

        if util < 1:
            clock = p.sm_clock_idle_mhz
        else:
            clock = p.sm_clock_max_mhz - 15 * rng.randint(0, 2)
            if ThrottleReason.SW_POWER_CAP in throttle:
                clock -= 15 * rng.randint(4, 10)
            if self._thermal_throttled:
                clock = round(clock * rng.uniform(0.55, 0.7))

        mem_used = 0
        if load_mem_gib > 0 or util > 0:
            mem_used = min(p.memory_total_mib, round(load_mem_gib * 1024) + DRIVER_OVERHEAD_MIB)

        new_sbe = poisson(rng, SBE_PER_GPU_PER_DAY * dt_s / 86_400 + fx.sbe_per_hour * dt_s / 3600)
        self._sbe += new_sbe
        self._sbe_since_retire += new_sbe
        if self._sbe_since_retire >= 40:  # repeated errors get the page retired
            self._retired += self._sbe_since_retire // 40
            self._sbe_since_retire %= 40
        self._dbe += fx.dbe
        self._pcie_replay += poisson(rng, fx.pcie_replays_per_min * dt_s / 60)
        self._nvlink_crc += poisson(rng, fx.nvlink_crc_per_min * dt_s / 60)

        if fx.offline:
            return None  # counters keep their state; the GPU just can't be read

        return GpuMetricsEvent(
            event_id=uuid.uuid4(),
            timestamp=at,
            node_id=self.node_id,
            gpu_index=self.gpu_index,
            gpu_uuid=self.gpu_uuid,
            gpu_model=p.model,
            temperature_c=round(min(temp_c, 120.0), 1),
            memory_temperature_c=round(min(temp_c + p.memory_temp_offset_c, 125.0), 1),
            power_w=round(power, 1),
            power_limit_w=round(limit, 1),
            utilization_pct=round(util, 1),
            memory_used_mib=mem_used,
            memory_total_mib=p.memory_total_mib,
            sm_clock_mhz=clock,
            throttle_reasons=throttle,
            ecc_sbe_total=self._sbe,
            ecc_dbe_total=self._dbe,
            retired_pages_total=self._retired,
            pcie_replay_total=self._pcie_replay,
            nvlink_crc_errors_total=self._nvlink_crc,
        )


@dataclass
class BmcNoise:
    """Benign BMC entries: short inlet-temperature warnings that assert, then clear."""

    node_id: str
    rng: random.Random

    def __post_init__(self) -> None:
        self._clears_at: float | None = None
        self._entry_id = self.rng.randint(1, 500)

    def _entry(self, at: datetime, code: BmcEntryCode, reading: float) -> BmcLogEvent:
        self._entry_id += 1
        asserted = code == BmcEntryCode.ASSERT
        return BmcLogEvent(
            event_id=uuid.uuid4(),
            timestamp=at,
            node_id=self.node_id,
            source_format=BmcSourceFormat.REDFISH,
            entry_id=str(self._entry_id),
            entry_type=BmcEntryType.SEL,
            severity=BmcSeverity.WARNING if asserted else BmcSeverity.OK,
            message_id="OpenBMC.0.1.SensorThresholdWarningHighGoingHigh"
            if asserted
            else "OpenBMC.0.1.SensorThresholdWarningHighGoingLow",
            message=f"Inlet_Temp sensor crossed a warning high threshold going "
            f"{'high' if asserted else 'low'}. Reading={reading:.1f} Threshold=35.0.",
            message_args=["Inlet_Temp", f"{reading:.1f}", "35.0"],
            sensor_type=BmcSensorType.TEMPERATURE,
            sensor_number=1,
            sensor_name="Inlet_Temp",
            entry_code=code,
            reading=round(reading, 1),
            unit="Cel",
        )

    def sample(self, t_s: float, dt_s: float, at: datetime) -> list[BmcLogEvent]:
        if self._clears_at is not None:
            if t_s >= self._clears_at:
                self._clears_at = None
                return [self._entry(at, BmcEntryCode.DEASSERT, self.rng.uniform(31, 34))]
            return []
        if self.rng.random() < INLET_WARNINGS_PER_NODE_PER_DAY * dt_s / 86_400:
            self._clears_at = t_s + self.rng.uniform(60, 600)
            return [self._entry(at, BmcEntryCode.ASSERT, self.rng.uniform(35.1, 37))]
        return []


class FleetSimulator:
    """Generates every event for the fleet, one sample interval at a time."""

    def __init__(
        self,
        fleet: Fleet,
        workload: Workload,
        start: datetime,
        seed: int = 0,
        offset_s: float = 0,
        node_prefix: str = "",
        faults: Sequence["Fault"] = (),
    ) -> None:
        self.fleet = fleet
        self.start = start
        self.offset_s = offset_s
        self.workload_s = workload.duration_s
        self.node_ids = [node_prefix + node.node_id for node in fleet.nodes]
        self.faults = list(faults)
        unknown = {f.node_id for f in self.faults} - set(self.node_ids)
        if unknown:
            raise ValueError(f"faults reference unknown nodes: {sorted(unknown)}")
        rng = random.Random(seed)
        self.gpus: list[GpuSimulator] = []
        self.bmc: list[BmcNoise] = []
        for i, node in enumerate(fleet.nodes):
            node_id = self.node_ids[i]
            # Each node gets a slightly different inlet temperature (position in the rack).
            inlet = fleet.inlet_temp_c + rng.uniform(-1.5, 2.5)
            for g in range(node.gpus):
                self.gpus.append(
                    GpuSimulator(
                        node_id=node_id,
                        gpu_index=g,
                        profile=node.profile,
                        load=workload.for_gpu(i, g),
                        inlet_c=inlet + rng.uniform(0, 2),  # GPUs further back run warmer
                        rng=random.Random(rng.getrandbits(64)),
                    )
                )
            self.bmc.append(BmcNoise(node_id, random.Random(rng.getrandbits(64))))

    def steps(self, duration_s: float) -> Iterator[tuple[float, list[TelemetryEvent]]]:
        """Yields (seconds since start, events) for each sample interval."""
        dt = self.fleet.sample_interval_s
        for k in range(int(duration_s // dt)):
            t = k * dt
            at = self.start + timedelta(seconds=t)
            # Loop the workload if the replay runs longer than the slice.
            t_load = (self.offset_s + t) % self.workload_s if self.workload_s else 0.0
            active = [f for f in self.faults if f.active(t)]
            events: list[TelemetryEvent] = []
            # Fault events first: some (e.g. the final DBE) change this interval's metrics.
            for fault in self.faults:
                events.extend(fault.events(t, dt, at))
            for gpu in self.gpus:
                fx = GpuEffects()
                for fault in active:
                    if fault.node_id == gpu.node_id and gpu.gpu_index in fault.gpus:
                        fault.apply(gpu.gpu_index, t, fx)
                sample = gpu.sample(t_load, dt, at, fx)
                if sample is not None:
                    events.append(sample)
            for node in self.bmc:
                events.extend(node.sample(t, dt, at))
            yield t, events
