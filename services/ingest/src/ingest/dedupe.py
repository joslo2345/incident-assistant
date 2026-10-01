"""Remembers recently seen event IDs so client retries don't produce duplicates.

This is a best-effort, single-process filter that keeps duplicates off the stream. The guarantee
comes from idempotent inserts keyed on event_id in the consumer (A2). With several API replicas,
or after a restart, a duplicate can get past this filter and is dropped there.
"""

import time
from collections import OrderedDict
from collections.abc import Callable, Iterable
from uuid import UUID


class RecentIds:
    def __init__(
        self, ttl_s: float, max_ids: int, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._ttl_s = ttl_s
        self._max_ids = max_ids
        self._clock = clock
        self._seen: OrderedDict[UUID, float] = OrderedDict()  # id -> time first seen, oldest first

    def __len__(self) -> int:
        return len(self._seen)

    def __contains__(self, event_id: UUID) -> bool:
        self._expire()
        return event_id in self._seen

    def add_all(self, event_ids: Iterable[UUID]) -> None:
        now = self._clock()
        for event_id in event_ids:
            self._seen[event_id] = now
            self._seen.move_to_end(event_id)
        while len(self._seen) > self._max_ids:
            self._seen.popitem(last=False)

    def _expire(self) -> None:
        cutoff = self._clock() - self._ttl_s
        while self._seen:
            oldest_time = next(iter(self._seen.values()))
            if oldest_time > cutoff:
                break
            self._seen.popitem(last=False)
