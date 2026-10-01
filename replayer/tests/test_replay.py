import asyncio
import json
from typing import Any

import httpx2
import pytest

from ingest.app import create_app
from ingest.settings import Settings
from replayer.replay import API_KEY_HEADER, ReplayError, ReplayStats, RetryPolicy, Sender, replay
from replayer.synth import FleetSimulator

KEY = "test-key"


def test_ten_minute_replay_end_to_end(sim: FleetSimulator) -> None:
    """The A1 integration test: 10 simulated minutes through the real ingest app."""
    app = create_app(Settings(api_keys=frozenset({KEY})))

    async def scenario() -> Any:
        async with app.router.lifespan_context(app):
            transport = httpx2.ASGITransport(app=app)
            async with httpx2.AsyncClient(
                transport=transport, base_url="http://ingest", headers={API_KEY_HEADER: KEY}
            ) as client:
                stats = await replay(sim, client, duration_s=600, speed=0, batch_size=500)
            return stats

    stats = asyncio.run(scenario())
    gpu_samples = 60 * 64  # 60 intervals x 64 GPUs
    assert stats.events_sent >= gpu_samples
    assert stats.accepted == stats.events_sent
    assert stats.duplicates == 0
    assert stats.retries_429 == stats.retries_error == 0
    assert len(app.state.publisher.events) == stats.accepted


def scripted_client(
    responses: list[httpx2.Response | Exception],
) -> tuple[httpx2.AsyncClient, list[Any]]:
    """Client whose transport returns `responses` in order and records request bodies."""
    bodies: list[Any] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        bodies.append(json.loads(request.content))
        r = responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler), base_url="http://ingest")
    return client, bodies


def ok(n: int) -> httpx2.Response:
    return httpx2.Response(202, json={"accepted": n, "duplicates": 0})


FAST = RetryPolicy(max_attempts=4, base_backoff_s=0.001, max_backoff_s=0.001)


def one_batch(sim: FleetSimulator) -> list[Any]:
    return next(sim.steps(10))[1][:5]


def test_retries_429_with_same_event_ids(sim: FleetSimulator) -> None:

    events = one_batch(sim)
    client, bodies = scripted_client(
        [httpx2.Response(429, headers={"Retry-After": "0"}), ok(len(events))]
    )
    stats = ReplayStats()
    asyncio.run(Sender(client, stats, FAST).send(events))
    assert stats.retries_429 == 1
    assert stats.accepted == len(events)
    assert bodies[0] == bodies[1], "a retry must resend identical events"


def test_retries_server_errors_and_network_failures(sim: FleetSimulator) -> None:

    events = one_batch(sim)
    client, _ = scripted_client(
        [httpx2.Response(503), httpx2.ConnectError("refused"), ok(len(events))]
    )
    stats = ReplayStats()
    asyncio.run(Sender(client, stats, FAST).send(events))
    assert stats.retries_error == 2
    assert stats.batches == 1


@pytest.mark.parametrize("status", [401, 422])
def test_client_errors_are_not_retried(sim: FleetSimulator, status: int) -> None:

    client, bodies = scripted_client([httpx2.Response(status, json={"error": "x"})])
    with pytest.raises(ReplayError, match=str(status)):
        asyncio.run(Sender(client, ReplayStats(), FAST).send(one_batch(sim)))
    assert len(bodies) == 1


def test_gives_up_after_max_attempts(sim: FleetSimulator) -> None:

    client, bodies = scripted_client([httpx2.Response(500)] * 4)
    with pytest.raises(ReplayError, match="after 4 attempts"):
        asyncio.run(Sender(client, ReplayStats(), FAST).send(one_batch(sim)))
    assert len(bodies) == 4


def test_replay_surfaces_send_failures(sim: FleetSimulator) -> None:
    client, _ = scripted_client([httpx2.Response(401, json={"error": "unauthorized"})] * 10)
    with pytest.raises(ReplayError, match="401"):
        asyncio.run(replay(sim, client, duration_s=60, speed=0, batch_size=64, concurrency=1))


def test_speed_paces_the_replay(sim: FleetSimulator) -> None:
    client, _ = scripted_client([ok(64) for _ in range(10)])
    # 50 s of sim time at 100x should take about 0.5 s of wall time (first sample at t=0).
    stats = asyncio.run(replay(sim, client, duration_s=50, speed=100, batch_size=1000))
    assert 0.35 <= stats.wall_seconds <= 1.0
    assert stats.batches == 5
