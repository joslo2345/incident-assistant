"""Writes incidents to the `incidents` table (upsert by incident_id)."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import asyncpg

from detector.incidents import OpenIncident

UPSERT = """
INSERT INTO incidents (incident_id, detector_run, node_id, gpu_indices, status, severity,
    suspected_failure_type, title, opened_at, first_signal_at, last_signal_at, record)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12::jsonb)
ON CONFLICT (incident_id) DO UPDATE SET
    gpu_indices = EXCLUDED.gpu_indices, status = EXCLUDED.status, severity = EXCLUDED.severity,
    suspected_failure_type = EXCLUDED.suspected_failure_type, title = EXCLUDED.title,
    opened_at = EXCLUDED.opened_at, last_signal_at = EXCLUDED.last_signal_at,
    record = EXCLUDED.record, written_at = now()
"""


def _row(run: str, inc: OpenIncident) -> tuple[Any, ...]:
    c = inc.to_contract()
    return (
        c.incident_id, run, inc.node_id, inc.gpus, c.status.value, c.severity.value,
        c.suspected_failure_type.value, c.title, c.created_at, inc.first_signal_at,
        inc.last_signal_at, c.model_dump_json(),
    )  # fmt: skip


async def save(
    conn: asyncpg.Connection[Any] | asyncpg.pool.PoolConnectionProxy[Any],
    run: str,
    incidents: Iterable[OpenIncident],
) -> int:
    rows = [_row(run, inc) for inc in incidents]
    if rows:
        await conn.executemany(UPSERT, rows)
    return len(rows)


async def clear_run(
    conn: asyncpg.Connection[Any] | asyncpg.pool.PoolConnectionProxy[Any], run: str
) -> None:
    await conn.execute("DELETE FROM incidents WHERE detector_run = $1", run)
