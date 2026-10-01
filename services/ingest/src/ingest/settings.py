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
    # Kafka bootstrap servers. Unset = keep events in memory (tests, running without Kafka).
    kafka_bootstrap: str | None = None
    kafka_topic: str = "telemetry.raw"
    # Max events being published at once before new batches get 429.
    max_in_flight: int = 50_000
    # Larger request bodies are rejected with 413 before they're parsed.
    max_body_bytes: int = 10 * 1024 * 1024
    # Seconds clients are told to wait (Retry-After) on 429 and 503.
    retry_after_s: int = 1
    # How long, and how many, event IDs are remembered for duplicate detection.
    dedupe_ttl_s: float = 600.0
    dedupe_max_ids: int = 1_000_000

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ.get
        return cls(
            api_keys=_keys_from_env(),
            kafka_bootstrap=env("KAFKA_BOOTSTRAP") or None,
            kafka_topic=env("KAFKA_TOPIC", cls.kafka_topic),
            max_in_flight=int(env("INGEST_MAX_IN_FLIGHT", cls.max_in_flight)),
            max_body_bytes=int(env("INGEST_MAX_BODY_BYTES", cls.max_body_bytes)),
            retry_after_s=int(env("INGEST_RETRY_AFTER_S", cls.retry_after_s)),
            dedupe_ttl_s=float(env("INGEST_DEDUPE_TTL_S", cls.dedupe_ttl_s)),
            dedupe_max_ids=int(env("INGEST_DEDUPE_MAX_IDS", cls.dedupe_max_ids)),
        )
