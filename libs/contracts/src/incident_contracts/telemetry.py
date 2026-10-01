"""Telemetry events sent by the replayer (or a customer's agents) to POST /v1/telemetry.

There are three event types, distinguished by `event_type`:

- `gpu_metrics`: one periodic sample of one GPU's sensors and counters (DCGM-style)
- `xid`: a driver-reported XID error for one GPU
- `bmc_log`: a baseboard management controller log entry in Redfish `LogEntry` shape
  (fans, PSUs, thermals), whether it was collected via Redfish, IPMI SEL, or syslog
"""

from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from incident_contracts.common import SCHEMA_VERSION, Contract, GpuIndex, NodeId

MAX_BATCH_SIZE = 1000


class _EventBase(Contract):
    # Client-generated, so retries can be deduplicated (idempotency).
    event_id: UUID
    # When the reading was taken at the source, not when it was received.
    timestamp: AwareDatetime
    node_id: NodeId
    schema_version: Literal["1.0"] = SCHEMA_VERSION


class ThrottleReason(StrEnum):
    """Subset of NVML clock-throttle reasons that matter for diagnosis."""

    SW_POWER_CAP = "sw_power_cap"
    HW_SLOWDOWN = "hw_slowdown"
    HW_THERMAL_SLOWDOWN = "hw_thermal_slowdown"
    HW_POWER_BRAKE_SLOWDOWN = "hw_power_brake_slowdown"
    SW_THERMAL_SLOWDOWN = "sw_thermal_slowdown"


class GpuMetricsEvent(_EventBase):
    event_type: Literal["gpu_metrics"] = "gpu_metrics"
    gpu_index: GpuIndex
    gpu_uuid: str | None = Field(default=None, max_length=64)
    gpu_model: str | None = Field(
        default=None, max_length=64, description='As DCGM reports it, e.g. "NVIDIA H100 80GB HBM3"'
    )

    # Thermals and power
    temperature_c: float = Field(ge=-20, le=150)
    memory_temperature_c: float | None = Field(default=None, ge=-20, le=150)
    power_w: float = Field(ge=0, le=2000)
    power_limit_w: float = Field(gt=0, le=2000)

    # Load
    utilization_pct: float = Field(ge=0, le=100)
    memory_used_mib: int = Field(ge=0)
    memory_total_mib: int = Field(gt=0)
    sm_clock_mhz: int = Field(ge=0, le=5000)
    throttle_reasons: list[ThrottleReason] = Field(default_factory=list)

    # Error counters: cumulative since driver load, so detectors should diff them.
    ecc_sbe_total: int = Field(ge=0, description="Correctable (single-bit) ECC errors")
    ecc_dbe_total: int = Field(ge=0, description="Uncorrectable (double-bit) ECC errors")
    retired_pages_total: int | None = Field(default=None, ge=0)
    pcie_replay_total: int | None = Field(default=None, ge=0)
    nvlink_crc_errors_total: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _memory_fits(self) -> Self:
        if self.memory_used_mib > self.memory_total_mib:
            raise ValueError("memory_used_mib cannot exceed memory_total_mib")
        return self


class XidEvent(_EventBase):
    event_type: Literal["xid"] = "xid"
    gpu_index: GpuIndex
    xid_code: int = Field(ge=1, le=999, description="NVIDIA XID error code, e.g. 79 = fell off bus")
    message: str = Field(max_length=2000)


class BmcSeverity(StrEnum):
    """Redfish `Severity`."""

    OK = "ok"
    WARNING = "warning"
    CRITICAL = "critical"


class BmcEntryType(StrEnum):
    """Redfish `EntryType`. `sel` entries are IPMI SEL records surfaced through Redfish."""

    EVENT = "event"
    SEL = "sel"
    OEM = "oem"


class BmcEntryCode(StrEnum):
    """Redfish `EntryCode` for sensor entries: whether a condition started or cleared."""

    ASSERT = "assert"
    DEASSERT = "deassert"


class BmcSensorType(StrEnum):
    """Subset of Redfish `SensorType` (the IPMI sensor types) relevant to GPU servers."""

    TEMPERATURE = "temperature"
    VOLTAGE = "voltage"
    CURRENT = "current"
    FAN = "fan"
    POWER_SUPPLY = "power_supply"  # Redfish "Power Supply / Converter"
    POWER_UNIT = "power_unit"
    PROCESSOR = "processor"
    MEMORY = "memory"
    CRITICAL_INTERRUPT = "critical_interrupt"  # e.g. PCIe fatal errors
    OTHER = "other"


class BmcSourceFormat(StrEnum):
    """How the entry was collected before being normalized to this schema."""

    REDFISH = "redfish"
    IPMI_SEL = "ipmi_sel"
    SYSLOG = "syslog"


class BmcLogEvent(_EventBase):
    """A BMC log entry, normalized to the shape of a DMTF Redfish `LogEntry`.

    `timestamp` is the entry's Redfish `Created` time.
    """

    event_type: Literal["bmc_log"] = "bmc_log"
    source_format: BmcSourceFormat = BmcSourceFormat.REDFISH
    entry_id: str | None = Field(default=None, max_length=64, description="Redfish `Id` on the BMC")
    entry_type: BmcEntryType
    severity: BmcSeverity
    message_id: str = Field(
        min_length=1,
        max_length=256,
        description='Registry message ID, e.g. "OpenBMC.0.1.SensorThresholdCriticalLowGoingLow" '
        'or a vendor ID like "PSU0003"',
    )
    message: str = Field(max_length=2000)
    message_args: list[str] = Field(default_factory=list, max_length=32)

    # Sensor fields: always present on SEL entries, optional otherwise.
    sensor_type: BmcSensorType | None = None
    sensor_number: int | None = Field(default=None, ge=0, le=255)
    sensor_name: str | None = Field(default=None, max_length=128, description='e.g. "FAN3", "PSU1"')
    entry_code: BmcEntryCode | None = None
    reading: float | None = None
    unit: str | None = Field(default=None, max_length=16, description='e.g. "RPM", "Cel", "W"')

    @model_validator(mode="after")
    def _sel_has_sensor(self) -> Self:
        if self.entry_type == BmcEntryType.SEL and (
            self.sensor_type is None or self.entry_code is None
        ):
            raise ValueError("SEL entries must include sensor_type and entry_code")
        return self


TelemetryEvent = Annotated[
    GpuMetricsEvent | XidEvent | BmcLogEvent,
    Field(discriminator="event_type"),
]


class TelemetryBatch(Contract):
    """Request body for POST /v1/telemetry."""

    events: list[TelemetryEvent] = Field(min_length=1, max_length=MAX_BATCH_SIZE)
