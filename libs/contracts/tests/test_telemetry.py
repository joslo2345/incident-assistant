from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from incident_contracts import (
    MAX_BATCH_SIZE,
    BmcLogEvent,
    GpuMetricsEvent,
    TelemetryBatch,
    TelemetryEvent,
    XidEvent,
)

event_adapter: TypeAdapter[TelemetryEvent] = TypeAdapter(TelemetryEvent)


def test_discriminator_picks_the_right_model(
    gpu_metrics: dict[str, Any], xid: dict[str, Any], bmc_log: dict[str, Any]
) -> None:
    assert isinstance(event_adapter.validate_python(gpu_metrics), GpuMetricsEvent)
    assert isinstance(event_adapter.validate_python(xid), XidEvent)
    assert isinstance(event_adapter.validate_python(bmc_log), BmcLogEvent)


def test_batch_round_trips_through_json(
    gpu_metrics: dict[str, Any], xid: dict[str, Any], bmc_log: dict[str, Any]
) -> None:
    batch = TelemetryBatch.model_validate({"events": [gpu_metrics, xid, bmc_log]})
    assert TelemetryBatch.model_validate_json(batch.model_dump_json()) == batch


def test_unknown_event_type_rejected(gpu_metrics: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        event_adapter.validate_python({**gpu_metrics, "event_type": "disk_smart"})


def test_naive_timestamp_rejected(gpu_metrics: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match="timezone"):
        GpuMetricsEvent.model_validate({**gpu_metrics, "timestamp": "2026-10-01T12:00:00"})


def test_unknown_field_rejected(gpu_metrics: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match="extra"):
        GpuMetricsEvent.model_validate({**gpu_metrics, "temprature_c": 70})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("temperature_c", 400),
        ("utilization_pct", 101),
        ("power_w", -1),
        ("gpu_index", 16),
        ("ecc_dbe_total", -1),
        ("node_id", "GPU NODE 1"),
    ],
)
def test_out_of_range_values_rejected(gpu_metrics: dict[str, Any], field: str, value: Any) -> None:
    with pytest.raises(ValidationError):
        GpuMetricsEvent.model_validate({**gpu_metrics, field: value})


def test_memory_used_cannot_exceed_total(gpu_metrics: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match="memory_used_mib"):
        GpuMetricsEvent.model_validate({**gpu_metrics, "memory_used_mib": 90_000})


def test_wrong_schema_version_rejected(xid: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        XidEvent.model_validate({**xid, "schema_version": "2.0"})


def test_batch_size_limits(gpu_metrics: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        TelemetryBatch.model_validate({"events": []})
    with pytest.raises(ValidationError):
        TelemetryBatch.model_validate({"events": [gpu_metrics] * (MAX_BATCH_SIZE + 1)})


def test_sel_entry_requires_sensor_fields(bmc_log: dict[str, Any]) -> None:
    for missing in ("sensor_type", "entry_code"):
        entry = {k: v for k, v in bmc_log.items() if k != missing}
        with pytest.raises(ValidationError, match="SEL entries"):
            BmcLogEvent.model_validate(entry)


def test_non_sensor_event_entry_is_valid(bmc_log: dict[str, Any]) -> None:
    sensor_fields = {"sensor_type", "sensor_number", "sensor_name", "entry_code", "reading", "unit"}
    entry = {k: v for k, v in bmc_log.items() if k not in sensor_fields}
    entry |= {
        "entry_type": "event",
        "severity": "warning",
        "message_id": "ResourceEvent.1.3.ResourceErrorsDetected",
        "message": "The resource property PSU1 has detected errors of type 'Input lost'.",
        "message_args": ["PSU1", "Input lost"],
    }
    parsed = BmcLogEvent.model_validate(entry)
    assert parsed.sensor_type is None


def test_vendor_message_id_and_ipmi_source_accepted(bmc_log: dict[str, Any]) -> None:
    parsed = BmcLogEvent.model_validate(
        {**bmc_log, "message_id": "PSU0003", "source_format": "ipmi_sel"}
    )
    assert parsed.message_id == "PSU0003"


def test_bmc_severity_uses_redfish_values(bmc_log: dict[str, Any]) -> None:
    BmcLogEvent.model_validate({**bmc_log, "severity": "ok"})
    with pytest.raises(ValidationError):
        BmcLogEvent.model_validate({**bmc_log, "severity": "info"})
