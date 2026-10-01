"""Publishes accepted events to the stream and waits until it has them.

The API answers 202 only after the publisher returns, so a 202 means the events are durable in
Kafka (acks=all), not just buffered in this process. Kafka publishing is keyed by node_id so each
node's events stay ordered within one partition.
"""

from collections.abc import Sequence
from typing import Protocol

from aiokafka import AIOKafkaProducer
from aiokafka.errors import KafkaError

from incident_contracts import TelemetryEvent


class PublishError(Exception):
    """The stream didn't confirm every event; the client should retry the batch."""


class Publisher(Protocol):
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def publish(self, events: Sequence[TelemetryEvent]) -> None: ...


class KafkaPublisher:
    def __init__(self, bootstrap: str, topic: str, timeout_ms: int = 10_000) -> None:
        self._bootstrap = bootstrap
        self._topic = topic
        self._timeout_ms = timeout_ms
        self._producer: AIOKafkaProducer | None = None

    async def start(self) -> None:
        # aiokafka binds to the running event loop, so create the producer here, not in __init__.
        self._producer = AIOKafkaProducer(
            bootstrap_servers=self._bootstrap,
            acks="all",
            enable_idempotence=True,  # broker drops producer-side retries it already has
            linger_ms=5,
            max_batch_size=256 * 1024,
            request_timeout_ms=self._timeout_ms,
        )
        await self._producer.start()

    async def stop(self) -> None:
        if self._producer is not None:
            await self._producer.stop()

    async def publish(self, events: Sequence[TelemetryEvent]) -> None:
        if self._producer is None:
            raise PublishError("publisher not started")
        try:
            # send() only queues the record; the returned futures resolve when the broker acks.
            acks = [
                await self._producer.send(
                    self._topic, value=e.model_dump_json().encode(), key=e.node_id.encode()
                )
                for e in events
            ]
            for ack in acks:
                await ack
        except KafkaError as exc:
            raise PublishError(str(exc)) from exc


class MemoryPublisher:
    """For tests and running the API without Kafka."""

    def __init__(self) -> None:
        self.events: list[TelemetryEvent] = []
        self.fail_next: Exception | None = None

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def publish(self, events: Sequence[TelemetryEvent]) -> None:
        if self.fail_next is not None:
            exc, self.fail_next = self.fail_next, None
            raise PublishError(str(exc))
        self.events.extend(events)
