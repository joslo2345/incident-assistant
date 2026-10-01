"""Where accepted events go after validation.

A1 uses an in-memory bounded buffer drained by a background task, so the API behaves like it
will in front of Kafka: when the buffer is full, it rejects instead of growing without limit.
A2 replaces the handler with a Kafka producer.
"""

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable, Sequence

from incident_contracts import TelemetryEvent

Handler = Callable[[Sequence[TelemetryEvent]], Awaitable[None]]


async def _discard(_: Sequence[TelemetryEvent]) -> None:
    return None


class BufferedSink:
    def __init__(self, capacity: int, handler: Handler = _discard, chunk_size: int = 1000) -> None:
        self.capacity = capacity
        self._handler = handler
        self._chunk_size = chunk_size
        self._buffer: deque[TelemetryEvent] = deque()
        self._ready = asyncio.Event()
        self.published = 0  # events handed to the handler so far

    def __len__(self) -> int:
        return len(self._buffer)

    def try_put(self, events: Sequence[TelemetryEvent]) -> bool:
        """Buffer all events, or none if they don't fit."""
        if len(self._buffer) + len(events) > self.capacity:
            return False
        self._buffer.extend(events)
        self._ready.set()
        return True

    async def run(self) -> None:
        """Drain the buffer forever. Cancelled on shutdown."""
        while True:
            await self._ready.wait()
            while self._buffer:
                n = min(self._chunk_size, len(self._buffer))
                chunk = [self._buffer.popleft() for _ in range(n)]
                await self._handler(chunk)
                self.published += n
            self._ready.clear()
