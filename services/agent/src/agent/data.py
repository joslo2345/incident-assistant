"""Read access to telemetry, incidents and the knowledge base, plus approval requests.

Everything here runs as the `incident_agent` database role (migration 007): SELECT on telemetry
and incidents, INSERT-only on approval requests. Deciding a request needs the separate
`approval_service` role (`ApprovalDecider`), which the agent process is never given in the loop.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal, Protocol

import asyncpg
import httpx2

from incident_contracts import Incident

ApprovalAction = Literal["drain_node", "reset_gpu"]


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    doc_id: str
    doc_kind: str
    title: str
    heading: str
    text: str
    score: float


class FleetData(Protocol):
    async def incident(self, incident_id: uuid.UUID) -> Incident | None: ...
    async def metrics(
        self, node_id: str, gpu_index: int | None, start: datetime, end: datetime, step: timedelta
    ) -> list[dict[str, Any]]: ...
    async def logs(
        self, node_id: str, start: datetime, end: datetime, contains: str | None, limit: int
    ) -> tuple[list[dict[str, Any]], int]: ...
    async def gpus(self, node_id: str) -> list[dict[str, Any]]: ...
    async def node_incidents(self, node_id: str, limit: int) -> list[dict[str, Any]]: ...


class Knowledge(Protocol):
    async def search(
        self, query: str, k: int, failure_type: str | None, doc_kind: str | None
    ) -> list[Chunk]: ...


class Approvals(Protocol):
    async def request(
        self,
        action: ApprovalAction,
        node_id: str,
        gpu_index: int | None,
        reason: str,
        requested_by: str,
        incident_id: uuid.UUID | None = None,
        run_id: uuid.UUID | None = None,
    ) -> tuple[dict[str, Any], bool]: ...
    async def for_node(self, node_id: str, limit: int) -> list[dict[str, Any]]: ...
    async def list(self, status: str | None, limit: int) -> list[dict[str, Any]]: ...


# Per-minute rollup, re-bucketed to `step` so a long window still comes back as a few dozen rows.
# Counters are cumulative, so max() per bucket shows their growth.
_METRICS_SERIES = """
SELECT time_bucket($5::interval, bucket) AS t,
       round(avg(temp_avg)::numeric, 1)           AS temp_avg_c,
       round(max(temp_max)::numeric, 1)           AS temp_max_c,
       round(avg(power_avg)::numeric, 0)          AS power_avg_w,
       round(max(power_limit_w)::numeric, 0)      AS power_limit_w,
       round(avg(util_avg)::numeric, 0)           AS util_avg_pct,
       min(sm_clock_min_mhz)                      AS sm_clock_min_mhz,
       sum(throttled_samples)::int                AS throttled_samples,
       sum(samples)::int                          AS samples,
       max(ecc_sbe_total)                         AS ecc_sbe_total,
       max(ecc_dbe_total)                         AS ecc_dbe_total,
       max(pcie_replay_total)                     AS pcie_replay_total,
       max(nvlink_crc_errors_total)               AS nvlink_crc_errors_total
FROM gpu_metrics_1m
WHERE node_id = $1 AND gpu_index = $2 AND bucket >= $3 AND bucket < $4
GROUP BY 1 ORDER BY 1
"""

# Whole-window summary per GPU, for a first look at a node.
_METRICS_SUMMARY = """
SELECT gpu_index,
       round(avg(temp_avg)::numeric, 1)                         AS temp_avg_c,
       round(max(temp_max)::numeric, 1)                         AS temp_max_c,
       round(avg(power_avg)::numeric, 0)                        AS power_avg_w,
       round(min(power_limit_w)::numeric, 0)                    AS power_limit_min_w,
       round(max(power_limit_w)::numeric, 0)                    AS power_limit_max_w,
       round(avg(util_avg)::numeric, 0)                         AS util_avg_pct,
       min(sm_clock_min_mhz)                                    AS sm_clock_min_mhz,
       sum(throttled_samples)::int                              AS throttled_samples,
       sum(samples)::int                                        AS samples,
       max(ecc_sbe_total) - min(ecc_sbe_total)                  AS ecc_sbe_increase,
       max(ecc_dbe_total) - min(ecc_dbe_total)                  AS ecc_dbe_increase,
       max(pcie_replay_total) - min(pcie_replay_total)          AS pcie_replay_increase,
       max(nvlink_crc_errors_total) - min(nvlink_crc_errors_total) AS nvlink_crc_increase,
       max(bucket)                                              AS last_minute
FROM gpu_metrics_1m
WHERE node_id = $1 AND bucket >= $2 AND bucket < $3
GROUP BY gpu_index ORDER BY gpu_index
"""

_LOGS = """
WITH entries AS (
    SELECT time, 'xid' AS source, gpu_index, 'error' AS severity,
           'XID ' || xid_code || ': ' || message AS text
    FROM xid_events WHERE node_id = $1 AND time >= $2 AND time < $3
    UNION ALL
    SELECT time, 'bmc', NULL, lower(severity),
           message || coalesce(' [sensor ' || sensor_name || ']', '')
                   || coalesce(' reading=' || reading || coalesce(' ' || unit, ''), '')
    FROM bmc_log WHERE node_id = $1 AND time >= $2 AND time < $3
)
SELECT time, source, gpu_index, severity, text, count(*) OVER () AS total
FROM entries
WHERE $4::text IS NULL OR text ILIKE '%' || $4 || '%' ESCAPE '\\'
ORDER BY time
LIMIT $5
"""

# Latest sample per GPU slot; each lookup is one index probe on (node_id, gpu_index, time DESC).
_GPUS = """
SELECT g.i AS gpu_index, m.time AS last_seen, m.gpu_model, m.gpu_uuid, m.memory_total_mib,
       m.power_limit_w
FROM generate_series(0, 15) AS g(i)
CROSS JOIN LATERAL (
    SELECT time, gpu_model, gpu_uuid, memory_total_mib, power_limit_w
    FROM gpu_metrics WHERE node_id = $1 AND gpu_index = g.i
    ORDER BY time DESC LIMIT 1
) m
ORDER BY g.i
"""


def _like_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _rows(records: list[asyncpg.Record]) -> list[dict[str, Any]]:
    return [dict(r) for r in records]


class PgFleetData:
    def __init__(self, pool: asyncpg.Pool[Any]) -> None:
        self.pool = pool

    async def incident(self, incident_id: uuid.UUID) -> Incident | None:
        record = await self.pool.fetchval(
            "SELECT record::text FROM incidents WHERE incident_id = $1", incident_id
        )
        return Incident.model_validate_json(record) if record else None

    async def metrics(
        self, node_id: str, gpu_index: int | None, start: datetime, end: datetime, step: timedelta
    ) -> list[dict[str, Any]]:
        if gpu_index is None:
            return _rows(await self.pool.fetch(_METRICS_SUMMARY, node_id, start, end))
        return _rows(await self.pool.fetch(_METRICS_SERIES, node_id, gpu_index, start, end, step))

    async def logs(
        self, node_id: str, start: datetime, end: datetime, contains: str | None, limit: int
    ) -> tuple[list[dict[str, Any]], int]:
        pattern = _like_escape(contains) if contains else None
        rows = _rows(await self.pool.fetch(_LOGS, node_id, start, end, pattern, limit))
        total = int(rows[0].pop("total")) if rows else 0
        for r in rows[1:]:
            r.pop("total")
        return rows, total

    async def gpus(self, node_id: str) -> list[dict[str, Any]]:
        return _rows(await self.pool.fetch(_GPUS, node_id))

    async def node_incidents(self, node_id: str, limit: int) -> list[dict[str, Any]]:
        return _rows(
            await self.pool.fetch(
                "SELECT incident_id, detector_run, status, severity, suspected_failure_type, "
                "title, opened_at FROM incidents WHERE node_id = $1 "
                "ORDER BY opened_at DESC LIMIT $2",
                node_id,
                limit,
            )
        )


_APPROVAL_COLUMNS = (
    "request_id, created_at, action, node_id, gpu_index, reason, incident_id, run_id, "
    "requested_by, status, decided_by, decided_at, decision_note"
)


class PgApprovals:
    def __init__(self, pool: asyncpg.Pool[Any]) -> None:
        self.pool = pool

    async def request(
        self,
        action: ApprovalAction,
        node_id: str,
        gpu_index: int | None,
        reason: str,
        requested_by: str,
        incident_id: uuid.UUID | None = None,
        run_id: uuid.UUID | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """File a request, or return the pending one for the same action and target."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                f"INSERT INTO approval_requests (request_id, action, node_id, gpu_index, reason, "
                f"incident_id, run_id, requested_by) VALUES ($1, $2, $3, $4, $5, $6, $7, $8) "
                f"ON CONFLICT (action, node_id, coalesce(gpu_index, -1)) WHERE status = 'pending' "
                f"DO NOTHING RETURNING {_APPROVAL_COLUMNS}",
                uuid.uuid4(), action, node_id, gpu_index, reason[:2000], incident_id, run_id,
                requested_by,
            )  # fmt: skip
            if row is not None:
                return dict(row), True
            existing = await conn.fetchrow(
                f"SELECT {_APPROVAL_COLUMNS} FROM approval_requests WHERE action = $1 AND "
                f"node_id = $2 AND coalesce(gpu_index, -1) = coalesce($3::smallint, -1) "
                f"AND status = 'pending'",
                action,
                node_id,
                gpu_index,
            )
        if existing is None:  # decided between our INSERT and SELECT; file a fresh one
            return await self.request(
                action, node_id, gpu_index, reason, requested_by, incident_id, run_id
            )
        return dict(existing), False

    async def for_node(self, node_id: str, limit: int) -> list[dict[str, Any]]:
        return _rows(
            await self.pool.fetch(
                f"SELECT {_APPROVAL_COLUMNS} FROM approval_requests WHERE node_id = $1 "
                f"ORDER BY created_at DESC LIMIT $2",
                node_id,
                limit,
            )
        )

    async def list(self, status: str | None, limit: int) -> list[dict[str, Any]]:
        return _rows(
            await self.pool.fetch(
                f"SELECT {_APPROVAL_COLUMNS} FROM approval_requests "
                f"WHERE $1::text IS NULL OR status = $1 ORDER BY created_at DESC LIMIT $2",
                status,
                limit,
            )
        )


class MemoryApprovals:
    """Approval requests kept in memory: for evaluation runs, so one run's requests can't leak
    into the next run's view of a node (get_node_inventory shows pending requests)."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    async def request(
        self,
        action: ApprovalAction,
        node_id: str,
        gpu_index: int | None,
        reason: str,
        requested_by: str,
        incident_id: uuid.UUID | None = None,
        run_id: uuid.UUID | None = None,
    ) -> tuple[dict[str, Any], bool]:
        for r in self.rows:
            if (r["action"], r["node_id"], r["gpu_index"]) == (action, node_id, gpu_index):
                return r, False
        row = {
            "request_id": uuid.uuid4(), "action": action, "node_id": node_id,
            "gpu_index": gpu_index, "reason": reason[:2000], "incident_id": incident_id,
            "run_id": run_id, "requested_by": requested_by, "status": "pending",
        }  # fmt: skip
        self.rows.append(row)
        return row, True

    async def for_node(self, node_id: str, limit: int) -> list[dict[str, Any]]:
        return [r for r in self.rows if r["node_id"] == node_id][:limit]

    async def list(self, status: str | None, limit: int) -> list[dict[str, Any]]:
        return [r for r in self.rows if status is None or r["status"] == status][:limit]


class DecisionError(Exception):
    pass


class ApprovalDecider:
    """Records human decisions. Runs as `approval_service`, which can't file requests."""

    def __init__(self, pool: asyncpg.Pool[Any]) -> None:
        self.pool = pool

    async def decide(
        self, request_id: uuid.UUID, approve: bool, decided_by: str, note: str | None
    ) -> dict[str, Any]:
        try:
            row = await self.pool.fetchrow(
                f"UPDATE approval_requests SET status = $2, decided_by = $3, decision_note = $4 "
                f"WHERE request_id = $1 RETURNING {_APPROVAL_COLUMNS}",
                request_id,
                "approved" if approve else "rejected",
                decided_by,
                note,
            )
        except asyncpg.RaiseError as exc:  # the trigger's checks
            raise DecisionError(str(exc)) from exc
        if row is None:
            raise DecisionError(f"no approval request {request_id}")
        return dict(row)


class HttpKnowledge:
    """The knowledge API from A4 (hybrid search + rerank), so the agent loads no models itself."""

    def __init__(self, client: httpx2.AsyncClient) -> None:
        self.client = client

    async def search(
        self, query: str, k: int, failure_type: str | None, doc_kind: str | None
    ) -> list[Chunk]:
        body: dict[str, Any] = {"query": query, "k": k}
        if failure_type:
            body["failure_type"] = failure_type
        if doc_kind:
            body["doc_kind"] = doc_kind
        response = await self.client.post("/v1/search", json=body)
        response.raise_for_status()
        return [
            Chunk(h["chunk_id"], h["doc_id"], h["doc_kind"], h["title"], h["heading"], h["text"],
                  h["score"])
            for h in response.json()["hits"]
        ]  # fmt: skip
