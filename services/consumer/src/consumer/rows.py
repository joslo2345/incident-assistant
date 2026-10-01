"""Kafka message -> validated event -> database row."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import TypeAdapter, ValidationError

from incident_contracts import BmcLogEvent, GpuMetricsEvent, TelemetryEvent, XidEvent

_event_adapter: TypeAdapter[TelemetryEvent] = TypeAdapter(TelemetryEvent)

GPU_METRICS_COLUMNS = (
    "time", "node_id", "gpu_index", "event_id", "gpu_uuid", "gpu_model", "temperature_c",
    "memory_temperature_c", "power_w", "power_limit_w", "utilization_pct", "memory_used_mib",
    "memory_total_mib", "sm_clock_mhz", "throttle_reasons", "ecc_sbe_total", "ecc_dbe_total",
    "retired_pages_total", "pcie_replay_total", "nvlink_crc_errors_total", "ingested_at",
)  # fmt: skip
XID_COLUMNS = (
    "time", "node_id", "gpu_index", "event_id", "xid_code", "message", "ingested_at",
)  # fmt: skip
BMC_COLUMNS = (
    "time", "node_id", "event_id", "source_format", "entry_id", "entry_type", "severity",
    "message_id", "message", "message_args", "sensor_type", "sensor_number", "sensor_name",
    "entry_code", "reading", "unit", "ingested_at",
)  # fmt: skip


def _gpu_row(e: GpuMetricsEvent, ingested_at: datetime) -> tuple[Any, ...]:
    return (
        e.timestamp, e.node_id, e.gpu_index, e.event_id, e.gpu_uuid, e.gpu_model,
        e.temperature_c, e.memory_temperature_c, e.power_w, e.power_limit_w, e.utilization_pct,
        e.memory_used_mib, e.memory_total_mib, e.sm_clock_mhz,
        [str(r) for r in e.throttle_reasons], e.ecc_sbe_total, e.ecc_dbe_total,
        e.retired_pages_total, e.pcie_replay_total, e.nvlink_crc_errors_total, ingested_at,
    )  # fmt: skip


def _xid_row(e: XidEvent, ingested_at: datetime) -> tuple[Any, ...]:
    return (e.timestamp, e.node_id, e.gpu_index, e.event_id, e.xid_code, e.message, ingested_at)


def _opt(value: object) -> str | None:
    return None if value is None else str(value)


def _bmc_row(e: BmcLogEvent, ingested_at: datetime) -> tuple[Any, ...]:
    return (
        e.timestamp, e.node_id, e.event_id, str(e.source_format), e.entry_id, str(e.entry_type),
        str(e.severity), e.message_id, e.message, list(e.message_args), _opt(e.sensor_type),
        e.sensor_number, e.sensor_name, _opt(e.entry_code), e.reading, e.unit, ingested_at,
    )  # fmt: skip


@dataclass(frozen=True)
class Table:
    name: str
    columns: tuple[str, ...]


TABLES: dict[type, tuple[Table, Callable[[Any, datetime], tuple[Any, ...]]]] = {
    GpuMetricsEvent: (Table("gpu_metrics", GPU_METRICS_COLUMNS), _gpu_row),
    XidEvent: (Table("xid_events", XID_COLUMNS), _xid_row),
    BmcLogEvent: (Table("bmc_log", BMC_COLUMNS), _bmc_row),
}


@dataclass
class Rejected:
    raw: bytes
    error: str


@dataclass
class Batch:
    """Rows grouped by table, plus messages that failed validation (bound for the DLQ)."""

    rows: dict[Table, list[tuple[Any, ...]]] = field(default_factory=dict)
    rejected: list[Rejected] = field(default_factory=list)

    @property
    def row_count(self) -> int:
        return sum(len(r) for r in self.rows.values())

    def add(self, raw: bytes, ingested_at: datetime) -> None:
        try:
            event = _event_adapter.validate_json(raw)
        except ValidationError as exc:
            self.rejected.append(Rejected(raw, exc.json(include_url=False)[:4000]))
            return
        table, to_row = TABLES[type(event)]
        self.rows.setdefault(table, []).append(to_row(event, ingested_at))
