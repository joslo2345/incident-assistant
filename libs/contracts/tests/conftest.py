from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


@pytest.fixture
def gpu_metrics() -> dict[str, Any]:
    return {
        "event_type": "gpu_metrics",
        "event_id": str(uuid4()),
        "timestamp": T0.isoformat(),
        "node_id": "gpu-node-01",
        "gpu_index": 3,
        "temperature_c": 71.5,
        "power_w": 612.0,
        "power_limit_w": 700.0,
        "utilization_pct": 97.0,
        "memory_used_mib": 70_000,
        "memory_total_mib": 81_559,
        "sm_clock_mhz": 1980,
        "ecc_sbe_total": 4,
        "ecc_dbe_total": 0,
    }


@pytest.fixture
def xid() -> dict[str, Any]:
    return {
        "event_type": "xid",
        "event_id": str(uuid4()),
        "timestamp": T0.isoformat(),
        "node_id": "gpu-node-01",
        "gpu_index": 3,
        "xid_code": 79,
        "message": "GPU has fallen off the bus.",
    }


@pytest.fixture
def bmc_log() -> dict[str, Any]:
    return {
        "event_type": "bmc_log",
        "event_id": str(uuid4()),
        "timestamp": T0.isoformat(),
        "node_id": "gpu-node-01",
        "source_format": "redfish",
        "entry_id": "142",
        "entry_type": "sel",
        "severity": "critical",
        "message_id": "OpenBMC.0.1.SensorThresholdCriticalLowGoingLow",
        "message": "Fan FAN3 reading of 0 RPM is below the critical threshold of 500 RPM.",
        "message_args": ["FAN3", "0", "500"],
        "sensor_type": "fan",
        "sensor_number": 51,
        "sensor_name": "FAN3",
        "entry_code": "assert",
        "reading": 0,
        "unit": "RPM",
    }


@pytest.fixture
def incident() -> dict[str, Any]:
    gpu = {"kind": "gpu", "node_id": "gpu-node-01", "gpu_index": 3}
    return {
        "incident_id": str(uuid4()),
        "created_at": T0.isoformat(),
        "updated_at": T0.isoformat(),
        "status": "open",
        "severity": "sev2",
        "title": "GPU 3 on gpu-node-01 overheating",
        "suspected_failure_type": "thermal_runaway",
        "components": [gpu],
        "evidence": [
            {
                "evidence_id": "ev-1",
                "detector": "zscore",
                "component": gpu,
                "signal": "temperature_c",
                "window_start": "2026-10-01T11:50:00Z",
                "window_end": "2026-10-01T12:00:00Z",
                "observed_value": 89.0,
                "score": 4.2,
                "description": "Temperature 4.2 sigma above this GPU's 1h baseline",
            }
        ],
    }


@pytest.fixture
def diagnosis() -> dict[str, Any]:
    return {
        "root_cause": "thermal_runaway",
        "confidence": 0.82,
        "summary": "FAN3 failed; GPU 3 is heating and thermal-throttling.",
        "evidence_ids": ["ev-1"],
        "citations": [{"chunk_id": "rb-thermal#2", "doc_id": "rb-thermal"}],
        "recommended_action": {
            "action": "drain_node",
            "target": {"kind": "node", "node_id": "gpu-node-01"},
            "rationale": "Drain before replacing FAN3 to avoid job failures.",
        },
        "model": "claude-sonnet-5-5",
        "created_at": T0.isoformat(),
    }
