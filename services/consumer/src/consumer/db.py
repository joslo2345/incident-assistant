"""Idempotent batch writes to TimescaleDB.

Each batch is COPYed into a session-local temp table, then moved with
INSERT ... ON CONFLICT DO NOTHING, all in one transaction. COPY is the fastest way into Postgres;
the conflict clause makes redelivered events (after a consumer crash) harmless.
"""

from __future__ import annotations

from typing import Any

import asyncpg
from asyncpg.pool import PoolConnectionProxy

from consumer.rows import Batch


async def write_batch(
    conn: asyncpg.Connection[Any] | PoolConnectionProxy[Any], batch: Batch
) -> dict[str, int]:
    """Returns {table: rows actually inserted}; rows already present are skipped."""
    inserted: dict[str, int] = {}
    async with conn.transaction():
        for table, rows in batch.rows.items():
            staging = f"staging_{table.name}"
            cols = ", ".join(table.columns)
            await conn.execute(
                f"CREATE TEMP TABLE IF NOT EXISTS {staging} "
                f"(LIKE {table.name} INCLUDING DEFAULTS) ON COMMIT DELETE ROWS"
            )
            await conn.copy_records_to_table(staging, records=rows, columns=list(table.columns))
            status = await conn.execute(
                f"INSERT INTO {table.name} ({cols}) SELECT {cols} FROM {staging} "
                "ON CONFLICT DO NOTHING"
            )
            inserted[table.name] = int(status.split()[-1])  # "INSERT 0 <n>"
    return inserted
