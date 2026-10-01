"""Consumes telemetry.raw and writes it to TimescaleDB with at-least-once delivery.

Loop: fetch a batch -> validate -> send invalid messages to the DLQ -> write valid rows in one
transaction -> commit Kafka offsets. Offsets are committed only after the database commit, so a
crash at any point means the batch is redelivered, and the unique index on (event_id, time)
turns the redelivered rows into no-ops. Database errors are retried indefinitely without committing:
falling behind is better than losing data.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import time
from datetime import UTC, datetime
from typing import Any

import asyncpg
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer, ConsumerRecord, TopicPartition
from aiokafka.errors import CommitFailedError
from prometheus_client import Counter, Gauge, Histogram, start_http_server

from consumer.db import write_batch
from consumer.rows import Batch
from consumer.settings import Settings

log = logging.getLogger("consumer")

MESSAGES = Counter("consumer_messages_total", "Messages consumed", ["result"])
ROWS = Counter("consumer_rows_total", "Rows written", ["table", "result"])
BATCH_SIZE = Histogram(
    "consumer_batch_messages", "Messages per batch", buckets=(1, 10, 100, 500, 1000, 2500, 5000)
)
WRITE_SECONDS = Histogram("consumer_write_seconds", "DB transaction time per batch")
E2E_SECONDS = Histogram(
    "consumer_end_to_end_seconds",
    "Kafka publish (ingest) to DB commit, per message",
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60),
)
LAG = Gauge("consumer_lag_messages", "Messages behind the end of the topic", ["partition"])
DB_ERRORS = Counter("consumer_db_errors_total", "Failed batch writes (retried)")


def _kafka_time(record: ConsumerRecord[Any, Any]) -> datetime:
    return datetime.fromtimestamp(record.timestamp / 1000, tz=UTC)


async def _write_with_retry(pool: asyncpg.Pool[Any], batch: Batch, stop: asyncio.Event) -> bool:
    """Write until it succeeds or we're asked to stop. Returns True if written."""
    delay = 0.5
    while not stop.is_set():
        try:
            started = time.perf_counter()
            async with pool.acquire() as conn:
                inserted = await write_batch(conn, batch)
            WRITE_SECONDS.observe(time.perf_counter() - started)
            for table, rows in batch.rows.items():
                n = inserted.get(table.name, 0)
                ROWS.labels(table.name, "inserted").inc(n)
                ROWS.labels(table.name, "duplicate").inc(len(rows) - n)
            return True
        except (asyncpg.PostgresError, OSError) as exc:
            DB_ERRORS.inc()
            log.warning("batch write failed (%s); retrying in %.1fs", exc, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)
    return False


async def run(settings: Settings, stop: asyncio.Event) -> None:
    pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=4)
    consumer = AIOKafkaConsumer(
        settings.topic,
        bootstrap_servers=settings.kafka_bootstrap,
        group_id=settings.group_id,
        enable_auto_commit=False,
        auto_offset_reset="earliest",
        max_poll_records=settings.max_batch,
    )
    dlq = AIOKafkaProducer(bootstrap_servers=settings.kafka_bootstrap, acks="all")
    await consumer.start()
    await dlq.start()
    log.info("consuming %s as group %s", settings.topic, settings.group_id)
    try:
        while not stop.is_set():
            fetched = await consumer.getmany(
                timeout_ms=settings.max_wait_ms, max_records=settings.max_batch
            )
            records = [r for rs in fetched.values() for r in rs]
            if not records:
                continue
            BATCH_SIZE.observe(len(records))

            batch = Batch()
            for r in records:
                batch.add(r.value, _kafka_time(r))
            for bad in batch.rejected:
                await dlq.send_and_wait(
                    settings.dlq_topic,
                    bad.raw,
                    headers=[
                        ("error", bad.error.encode()),
                        ("source_topic", settings.topic.encode()),
                    ],
                )
            MESSAGES.labels("invalid").inc(len(batch.rejected))

            if batch.row_count and not await _write_with_retry(pool, batch, stop):
                break  # shutting down mid-retry: don't commit, the batch will be redelivered

            try:
                await consumer.commit()
            except CommitFailedError:
                # Partitions were reassigned mid-batch. The new owner re-reads from the last
                # commit; rows we already wrote become no-ops there.
                log.warning("commit failed after rebalance; batch will be redelivered")
                continue
            MESSAGES.labels("valid").inc(batch.row_count)
            now = time.time()
            for r in records:
                E2E_SECONDS.observe(max(0.0, now - r.timestamp / 1000))
            _update_lag(consumer, fetched)
    finally:
        await consumer.stop()
        await dlq.stop()
        await pool.close()


def _update_lag(
    consumer: AIOKafkaConsumer, fetched: dict[TopicPartition, list[ConsumerRecord[Any, Any]]]
) -> None:
    for tp, records in fetched.items():
        end = consumer.highwater(tp)
        if end is not None and records:
            LAG.labels(str(tp.partition)).set(max(0, end - records[-1].offset - 1))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = Settings.from_env()
    start_http_server(settings.metrics_port)

    async def _main() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        await run(settings, stop)

    asyncio.run(_main())


if __name__ == "__main__":
    main()
