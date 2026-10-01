import asyncio
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx2
import pytest
from fastapi.testclient import TestClient

from incident_contracts import TelemetryEvent
from ingest.app import API_KEY_HEADER, create_app
from ingest.publisher import MemoryPublisher
from ingest.settings import Settings

KEY = "test-key"


def make_client(**overrides: Any) -> TestClient:
    return TestClient(create_app(Settings(api_keys=frozenset({KEY}), **overrides)))


@pytest.fixture
def client() -> Iterator[TestClient]:
    with make_client() as c:  # `with` runs the lifespan (publisher start/stop)
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


def test_too_many_in_flight_returns_429_and_batch_is_retryable() -> None:
    publisher = MemoryPublisher()
    with TestClient(
        create_app(Settings(api_keys=frozenset({KEY}), max_in_flight=2, retry_after_s=3), publisher)
    ) as client:
        batch = {"events": [xid_event(), xid_event(), xid_event()]}
        resp = post(client, batch)
        assert resp.status_code == 429
        assert resp.headers["Retry-After"] == "3"
        assert resp.json()["error"] == "overloaded"
        assert publisher.events == []
        # Not remembered as seen: a smaller retry of the same events is accepted, not "duplicate".
        assert post(client, {"events": batch["events"][:2]}).json() == {
            "accepted": 2,
            "duplicates": 0,
        }


def test_concurrent_batches_share_the_in_flight_limit() -> None:
    gate = asyncio.Event()

    class SlowPublisher(MemoryPublisher):
        async def publish(self, events: Sequence[TelemetryEvent]) -> None:
            await gate.wait()
            await super().publish(events)

    app = create_app(Settings(api_keys=frozenset({KEY}), max_in_flight=3), SlowPublisher())

    async def scenario() -> list[int]:
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(
            transport=transport, base_url="http://t", headers={API_KEY_HEADER: KEY}
        ) as c:
            first = asyncio.create_task(
                c.post("/v1/telemetry", json={"events": [xid_event(), xid_event()]})
            )
            await asyncio.sleep(0.05)  # first batch is now waiting on the publisher
            second = await c.post("/v1/telemetry", json={"events": [xid_event(), xid_event()]})
            gate.set()
            return [(await first).status_code, second.status_code]

    assert asyncio.run(scenario()) == [202, 429]


def test_stream_failure_returns_503_and_batch_is_retryable() -> None:
    publisher = MemoryPublisher()
    with TestClient(create_app(Settings(api_keys=frozenset({KEY})), publisher)) as client:
        batch = {"events": [xid_event()]}
        publisher.fail_next = RuntimeError("broker down")
        resp = post(client, batch)
        assert resp.status_code == 503
        assert resp.json()["error"] == "unavailable"
        assert "Retry-After" in resp.headers
        assert post(client, batch).json() == {"accepted": 1, "duplicates": 0}
        assert len(publisher.events) == 1


def test_accepted_events_reach_the_publisher() -> None:
    publisher = MemoryPublisher()
    with TestClient(create_app(Settings(api_keys=frozenset({KEY})), publisher)) as client:
        events = [xid_event(), xid_event()]
        post(client, {"events": [*events, events[0]]})
        assert [str(e.event_id) for e in publisher.events] == [e["event_id"] for e in events]


def test_metrics_endpoint_exposes_counters(client: TestClient) -> None:
    post(client, {"events": [xid_event()]})
    body = client.get("/metrics").text
    assert "ingest_events_total" in body
    assert 'ingest_requests_total{path="/v1/telemetry",status="202"}' in body


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
    assert set(post_op["responses"]) >= {"202", "401", "422", "429", "503"}
    assert spec["components"]["securitySchemes"]["APIKeyHeader"]["name"] == API_KEY_HEADER


def test_oversized_body_rejected_before_parsing() -> None:
    with TestClient(create_app(Settings(api_keys=frozenset({KEY}), max_body_bytes=1000))) as c:
        big = {"events": [xid_event() for _ in range(20)]}  # ~5 KB
        # No API key: the size check runs first, so nothing is parsed for anonymous clients.
        resp = c.post("/v1/telemetry", json=big)
        assert resp.status_code == 413
        assert resp.json()["error"] == "too_large"


def test_oversized_chunked_body_rejected() -> None:
    def chunks() -> Iterator[bytes]:
        for _ in range(10):
            yield b" " * 500  # no Content-Length header: sent chunked

    with TestClient(create_app(Settings(api_keys=frozenset({KEY}), max_body_bytes=1000))) as c:
        resp = c.post(
            "/v1/telemetry",
            content=chunks(),
            headers={API_KEY_HEADER: KEY, "Content-Type": "application/json"},
        )
        assert resp.status_code == 413


def test_503_does_not_leak_broker_details() -> None:
    publisher = MemoryPublisher()
    with TestClient(create_app(Settings(api_keys=frozenset({KEY})), publisher)) as client:
        publisher.fail_next = RuntimeError("KafkaConnectionError: redpanda:9092 refused")
        body = post(client, {"events": [xid_event()]}).json()
        assert "redpanda" not in body["message"] and "9092" not in body["message"]
