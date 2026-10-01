import asyncio
from collections.abc import Sequence
from uuid import uuid4

from incident_contracts import TelemetryEvent
from ingest.dedupe import RecentIds
from ingest.sink import BufferedSink


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_ids_expire_after_ttl() -> None:
    clock = FakeClock()
    ids = RecentIds(ttl_s=10, max_ids=100, clock=clock)
    a = uuid4()
    ids.add_all([a])
    clock.now = 9.9
    assert a in ids
    clock.now = 10.0
    assert a not in ids
    assert len(ids) == 0


def test_oldest_ids_evicted_at_capacity() -> None:
    ids = RecentIds(ttl_s=60, max_ids=2, clock=FakeClock())
    a, b, c = uuid4(), uuid4(), uuid4()
    ids.add_all([a, b, c])
    assert a not in ids
    assert b in ids and c in ids


def test_sink_rejects_whole_batch_when_it_does_not_fit() -> None:
    sink = BufferedSink(capacity=3)
    events: list[TelemetryEvent] = [object()] * 2  # type: ignore[list-item]
    assert sink.try_put(events)
    assert not sink.try_put(events)
    assert len(sink) == 2


def test_sink_drains_to_handler_in_order() -> None:
    received: list[object] = []

    async def handler(chunk: Sequence[TelemetryEvent]) -> None:
        received.extend(chunk)

    async def scenario() -> None:
        sink = BufferedSink(capacity=10, handler=handler, chunk_size=2)
        task = asyncio.create_task(sink.run())
        sink.try_put([1, 2, 3])  # type: ignore[list-item]
        await asyncio.sleep(0.01)
        sink.try_put([4])  # type: ignore[list-item]
        await asyncio.sleep(0.01)
        task.cancel()
        assert len(sink) == 0
        assert sink.published == 4

    asyncio.run(scenario())
    assert received == [1, 2, 3, 4]
