from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from ingest.app import API_KEY_HEADER, app, configured_api_keys

KEY = "test-key"


@pytest.fixture
def client() -> Iterator[TestClient]:
    app.dependency_overrides[configured_api_keys] = lambda: frozenset({KEY})
    yield TestClient(app)
    app.dependency_overrides.clear()


def xid_event(event_id: str | None = None) -> dict[str, Any]:
    return {
        "event_type": "xid",
        "event_id": event_id or str(uuid4()),
        "timestamp": datetime(2026, 10, 1, 12, tzinfo=UTC).isoformat(),
        "node_id": "gpu-node-01",
        "gpu_index": 0,
        "xid_code": 79,
        "message": "GPU has fallen off the bus.",
    }


def post(client: TestClient, body: Any, key: str | None = KEY) -> Any:
    headers = {API_KEY_HEADER: key} if key is not None else {}
    return client.post("/v1/telemetry", json=body, headers=headers)


def test_accepts_valid_batch(client: TestClient) -> None:
    resp = post(client, {"events": [xid_event(), xid_event()]})
    assert resp.status_code == 202
    assert resp.json() == {"accepted": 2, "duplicates": 0}


def test_counts_in_batch_duplicates(client: TestClient) -> None:
    eid = str(uuid4())
    resp = post(client, {"events": [xid_event(eid), xid_event(eid)]})
    assert resp.json() == {"accepted": 1, "duplicates": 1}


@pytest.mark.parametrize("key", [None, "wrong-key"])
def test_rejects_missing_or_bad_key(client: TestClient, key: str | None) -> None:
    resp = post(client, {"events": [xid_event()]}, key=key)
    assert resp.status_code == 401
    assert resp.json()["error"] == "unauthorized"


def test_rejects_all_keys_when_none_configured(client: TestClient) -> None:
    app.dependency_overrides[configured_api_keys] = lambda: frozenset()
    assert post(client, {"events": [xid_event()]}).status_code == 401


def test_rejects_invalid_event(client: TestClient) -> None:
    resp = post(client, {"events": [{**xid_event(), "xid_code": 0}]})
    assert resp.status_code == 422


def test_healthz_needs_no_key(client: TestClient) -> None:
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_openapi_documents_contract() -> None:
    spec = app.openapi()
    post_op = spec["paths"]["/v1/telemetry"]["post"]
    assert set(post_op["responses"]) >= {"202", "401", "422", "429"}
    assert spec["components"]["securitySchemes"]["APIKeyHeader"]["name"] == API_KEY_HEADER
