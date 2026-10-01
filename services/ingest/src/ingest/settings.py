"""Service settings, read from INGEST_* environment variables."""

import os
from dataclasses import dataclass, field


def _keys_from_env() -> frozenset[str]:
    raw = os.environ.get("INGEST_API_KEYS", "")
    return frozenset(k.strip() for k in raw.split(",") if k.strip())


@dataclass(frozen=True)
class Settings:
    # Valid API keys. None configured = every request is rejected.
    api_keys: frozenset[str] = field(default_factory=frozenset)
    # Max events buffered between the API and the sink before returning 429.
    queue_capacity: int = 50_000
    # Seconds clients are told to wait (Retry-After) when the queue is full.
    retry_after_s: int = 1
    # How long, and how many, event IDs are remembered for duplicate detection.
    dedupe_ttl_s: float = 600.0
    dedupe_max_ids: int = 1_000_000

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ.get
        return cls(
            api_keys=_keys_from_env(),
            queue_capacity=int(env("INGEST_QUEUE_CAPACITY", cls.queue_capacity)),
            retry_after_s=int(env("INGEST_RETRY_AFTER_S", cls.retry_after_s)),
            dedupe_ttl_s=float(env("INGEST_DEDUPE_TTL_S", cls.dedupe_ttl_s)),
            dedupe_max_ids=int(env("INGEST_DEDUPE_MAX_IDS", cls.dedupe_max_ids)),
        )
