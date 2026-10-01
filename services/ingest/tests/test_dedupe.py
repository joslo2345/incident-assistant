from uuid import uuid4

from ingest.dedupe import RecentIds


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
