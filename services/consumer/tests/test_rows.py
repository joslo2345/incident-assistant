import json
from datetime import UTC, datetime
from uuid import uuid4

from consumer.main import run  # noqa: F401  (import smoke test: module loads at runtime)
from consumer.rows import BMC_COLUMNS, GPU_METRICS_COLUMNS, XID_COLUMNS, Batch

NOW = datetime(2026, 10, 1, 12, tzinfo=UTC)


def gpu_event(**overrides: object) -> bytes:
    event = {
        "event_type": "gpu_metrics",
        "event_id": str(uuid4()),
        "timestamp": NOW.isoformat(),
        "node_id": "h100-node-01",
        "gpu_index": 2,
        "gpu_model": "NVIDIA H100 80GB HBM3",
        "temperature_c": 65.0,
        "power_w": 690.0,
        "power_limit_w": 700.0,
        "utilization_pct": 99.0,
        "memory_used_mib": 1000,
        "memory_total_mib": 81559,
        "sm_clock_mhz": 1900,
        "throttle_reasons": ["sw_power_cap"],
        "ecc_sbe_total": 3,
        "ecc_dbe_total": 0,
        **overrides,
    }
    return json.dumps(event).encode()


def test_rows_are_grouped_by_table_with_matching_columns() -> None:
    xid = {
        "event_type": "xid", "event_id": str(uuid4()), "timestamp": NOW.isoformat(),
        "node_id": "h100-node-01", "gpu_index": 2, "xid_code": 79, "message": "fell off bus",
    }  # fmt: skip
    bmc = {
        "event_type": "bmc_log", "event_id": str(uuid4()), "timestamp": NOW.isoformat(),
        "node_id": "h100-node-01", "entry_type": "event", "severity": "warning",
        "message_id": "ResourceEvent.1.3.ResourceErrorsDetected", "message": "PSU1 input lost",
    }  # fmt: skip
    batch = Batch()
    for raw in (gpu_event(), json.dumps(xid).encode(), json.dumps(bmc).encode()):
        batch.add(raw, NOW)

    assert batch.row_count == 3 and not batch.rejected
    by_name = {t.name: rows for t, rows in batch.rows.items()}
    assert len(by_name["gpu_metrics"][0]) == len(GPU_METRICS_COLUMNS)
    assert len(by_name["xid_events"][0]) == len(XID_COLUMNS)
    assert len(by_name["bmc_log"][0]) == len(BMC_COLUMNS)

    gpu_row = dict(zip(GPU_METRICS_COLUMNS, by_name["gpu_metrics"][0], strict=True))
    assert gpu_row["throttle_reasons"] == ["sw_power_cap"]
    assert gpu_row["ingested_at"] == NOW
    bmc_row = dict(zip(BMC_COLUMNS, by_name["bmc_log"][0], strict=True))
    assert bmc_row["severity"] == "warning" and bmc_row["sensor_type"] is None


def test_invalid_messages_are_rejected_not_raised() -> None:
    batch = Batch()
    batch.add(b"not json", NOW)
    batch.add(gpu_event(temperature_c=999), NOW)
    batch.add(gpu_event(), NOW)
    assert batch.row_count == 1
    assert batch.rejected[0].raw == b"not json"
    assert "temperature_c" in batch.rejected[1].error
