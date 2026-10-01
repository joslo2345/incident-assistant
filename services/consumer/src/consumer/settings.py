"""Consumer settings, read from CONSUMER_* / KAFKA_* / DATABASE_URL environment variables."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    kafka_bootstrap: str = "localhost:19092"
    topic: str = "telemetry.raw"
    dlq_topic: str = "telemetry.dlq"
    group_id: str = "telemetry-writer"
    database_url: str = "postgresql://postgres:postgres@localhost:5432/telemetry"
    # A batch is written when it reaches max_batch messages or max_wait_ms has passed.
    max_batch: int = 5000
    max_wait_ms: int = 500
    metrics_port: int = 9101

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ.get
        return cls(
            kafka_bootstrap=env("KAFKA_BOOTSTRAP", cls.kafka_bootstrap),
            topic=env("KAFKA_TOPIC", cls.topic),
            dlq_topic=env("KAFKA_DLQ_TOPIC", cls.dlq_topic),
            group_id=env("CONSUMER_GROUP_ID", cls.group_id),
            database_url=env("DATABASE_URL", cls.database_url),
            max_batch=int(env("CONSUMER_MAX_BATCH", cls.max_batch)),
            max_wait_ms=int(env("CONSUMER_MAX_WAIT_MS", cls.max_wait_ms)),
            metrics_port=int(env("CONSUMER_METRICS_PORT", cls.metrics_port)),
        )
