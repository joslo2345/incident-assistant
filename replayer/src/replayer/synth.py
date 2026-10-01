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
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta

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

SBE_PER_GPU_PER_DAY = 0.5  # healthy HBM sees the occasional corrected error
INLET_WARNINGS_PER_NODE_PER_DAY = 1.0  # benign BMC noise: brief inlet-temp warnings
DRIVER_OVERHEAD_MIB = 512


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
        self._sbe = self.rng.randint(0, 3)
        self._pcie_replay = self.rng.randint(0, 2)

    def sample(self, t_s: float, dt_s: float, at: datetime) -> GpuMetricsEvent:
        p, rng = self.profile, self.rng
        load_util, load_mem_gib = self.load.at(t_s)

        # AR(1) wander so a busy GPU drifts between ~90% and 100% rather than sitting flat.
        self._wander = 0.9 * self._wander + rng.gauss(0, 0.02)
        util = min(100.0, load_util * (1 + self._wander)) if load_util > 0 else 0.0
        util = max(0.0, util)

        power = p.power_idle_w + (p.power_limit_w - p.power_idle_w) * (util / 100) ** 0.9
        power *= 1 + rng.gauss(0, 0.015)
        throttle: list[ThrottleReason] = []
        if power >= 0.98 * p.power_limit_w:
            power = p.power_limit_w * rng.uniform(0.97, 1.0)
            throttle.append(ThrottleReason.SW_POWER_CAP)

        target_c = self.inlet_c + p.thermal_c_per_w * power
        if self._temp_c is None:
            self._temp_c = target_c
        self._temp_c += (target_c - self._temp_c) * (1 - math.exp(-dt_s / p.thermal_tau_s))
        temp_c = self._temp_c + rng.gauss(0, 0.3)

        if util < 1:
            clock = p.sm_clock_idle_mhz
        else:
            clock = p.sm_clock_max_mhz - 15 * rng.randint(0, 2)
            if throttle:
                clock -= 15 * rng.randint(4, 10)

        mem_used = 0
        if load_mem_gib > 0 or util > 0:
            mem_used = min(p.memory_total_mib, round(load_mem_gib * 1024) + DRIVER_OVERHEAD_MIB)

        if rng.random() < SBE_PER_GPU_PER_DAY * dt_s / 86_400:
            self._sbe += 1

        return GpuMetricsEvent(
            event_id=uuid.uuid4(),
            timestamp=at,
            node_id=self.node_id,
            gpu_index=self.gpu_index,
            gpu_uuid=self.gpu_uuid,
            gpu_model=p.model,
            temperature_c=round(temp_c, 1),
            memory_temperature_c=round(temp_c + p.memory_temp_offset_c, 1),
            power_w=round(power, 1),
            power_limit_w=p.power_limit_w,
            utilization_pct=round(util, 1),
            memory_used_mib=mem_used,
            memory_total_mib=p.memory_total_mib,
            sm_clock_mhz=clock,
            throttle_reasons=throttle,
            ecc_sbe_total=self._sbe,
            ecc_dbe_total=0,
            retired_pages_total=0,
            pcie_replay_total=self._pcie_replay,
            nvlink_crc_errors_total=0,
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
        self, fleet: Fleet, workload: Workload, start: datetime, seed: int = 0, offset_s: float = 0
    ) -> None:
        self.fleet = fleet
        self.start = start
        self.offset_s = offset_s
        self.workload_s = workload.duration_s
        rng = random.Random(seed)
        self.gpus: list[GpuSimulator] = []
        self.bmc: list[BmcNoise] = []
        for i, node in enumerate(fleet.nodes):
            # Each node gets a slightly different inlet temperature (position in the rack).
            inlet = fleet.inlet_temp_c + rng.uniform(-1.5, 2.5)
            for g in range(node.gpus):
                self.gpus.append(
                    GpuSimulator(
                        node_id=node.node_id,
                        gpu_index=g,
                        profile=node.profile,
                        load=workload.for_gpu(i, g),
                        inlet_c=inlet + rng.uniform(0, 2),  # GPUs further back run warmer
                        rng=random.Random(rng.getrandbits(64)),
                    )
                )
            self.bmc.append(BmcNoise(node.node_id, random.Random(rng.getrandbits(64))))

    def steps(self, duration_s: float) -> Iterator[tuple[float, list[TelemetryEvent]]]:
        """Yields (seconds since start, events) for each sample interval."""
        dt = self.fleet.sample_interval_s
        for k in range(int(duration_s // dt)):
            t = k * dt
            at = self.start + timedelta(seconds=t)
            # Loop the workload if the replay runs longer than the slice.
            t_load = (self.offset_s + t) % self.workload_s if self.workload_s else 0.0
            events: list[TelemetryEvent] = [gpu.sample(t_load, dt, at) for gpu in self.gpus]
            for node in self.bmc:
                events.extend(node.sample(t, dt, at))
            yield t, events
