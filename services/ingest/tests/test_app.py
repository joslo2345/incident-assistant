from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ingest.app import API_KEY_HEADER, create_app
from ingest.settings import Settings

KEY = "test-key"


def make_client(**overrides: Any) -> TestClient:
    return TestClient(create_app(Settings(api_keys=frozenset({KEY}), **overrides)))


@pytest.fixture
def client() -> Iterator[TestClient]:
    with make_client() as c:  # `with` runs the lifespan, so the sink drains
        yield c


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


def test_retried_batch_is_all_duplicates(client: TestClient) -> None:
    batch = {"events": [xid_event(), xid_event()]}
    assert post(client, batch).json() == {"accepted": 2, "duplicates": 0}
    assert post(client, batch).json() == {"accepted": 0, "duplicates": 2}


def test_partial_overlap_with_earlier_batch(client: TestClient) -> None:
    first, second = xid_event(), xid_event()
    post(client, {"events": [first]})
    assert post(client, {"events": [first, second]}).json() == {"accepted": 1, "duplicates": 1}


def test_full_queue_returns_429_and_batch_is_retryable() -> None:
    # No `with`: lifespan doesn't run, so nothing drains and the queue stays full.
    client = make_client(queue_capacity=2, retry_after_s=3)
    assert post(client, {"events": [xid_event(), xid_event()]}).status_code == 202

    batch = {"events": [xid_event()]}
    resp = post(client, batch)
    assert resp.status_code == 429
    assert resp.headers["Retry-After"] == "3"
    assert resp.json()["error"] == "overloaded"

    # Free space, then the same batch must be accepted, not counted as a duplicate.
    app: FastAPI = client.app  # type: ignore[assignment]
    app.state.sink._buffer.clear()
    assert post(client, batch).json() == {"accepted": 1, "duplicates": 0}


def test_batch_of_only_duplicates_is_not_throttled() -> None:
    client = make_client(queue_capacity=1)
    batch = {"events": [xid_event()]}
    assert post(client, batch).status_code == 202
    # Queue is full, but nothing new needs to go into it.
    assert post(client, batch).json() == {"accepted": 0, "duplicates": 1}


@pytest.mark.parametrize("key", [None, "wrong-key"])
def test_rejects_missing_or_bad_key(client: TestClient, key: str | None) -> None:
    resp = post(client, {"events": [xid_event()]}, key=key)
    assert resp.status_code == 401
    assert resp.json()["error"] == "unauthorized"


def test_rejects_all_keys_when_none_configured() -> None:
    client = TestClient(create_app(Settings(api_keys=frozenset())))
    assert post(client, {"events": [xid_event()]}).status_code == 401


def test_rejects_invalid_event(client: TestClient) -> None:
    resp = post(client, {"events": [{**xid_event(), "xid_code": 0}]})
    assert resp.status_code == 422


def test_healthz_needs_no_key(client: TestClient) -> None:
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_openapi_documents_contract() -> None:
    spec = create_app(Settings()).openapi()
    post_op = spec["paths"]["/v1/telemetry"]["post"]
    assert set(post_op["responses"]) >= {"202", "401", "422", "429"}
    assert spec["components"]["securitySchemes"]["APIKeyHeader"]["name"] == API_KEY_HEADER
