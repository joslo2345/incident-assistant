"""What the detector reads: per-GPU minute features plus XID and BMC events.

Features are aggregated from raw gpu_metrics (not the gpu_metrics_1m rollup) because the
detector needs to tell thermal throttling from power capping, which the rollup merges.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import asyncpg

MINUTE = timedelta(minutes=1)


@dataclass(frozen=True)
class GpuMinute:
    minute: datetime
    node_id: str
    gpu_index: int
    gpu_model: str | None
    samples: int
    temp_avg: float
    temp_max: float
    power_avg: float
    power_limit: float
    util_avg: float
    clock_min: int
    thermal_throttled: int  # samples with a thermal slowdown reason
    power_capped: int  # samples with sw_power_cap
    sbe: int
    dbe: int
    retired: int | None
    pcie: int | None
    nvlink: int | None


@dataclass(frozen=True)
class XidRecord:
    time: datetime
    node_id: str
    gpu_index: int
    code: int
    message: str


@dataclass(frozen=True)
class BmcRecord:
    time: datetime
    node_id: str
    severity: str
    message_id: str
    message: str
    sensor_type: str | None
    sensor_name: str | None
    entry_code: str | None
    reading: float | None


@dataclass
class MinuteBatch:
    """Everything that happened in one minute."""

    minute: datetime
    gpus: list[GpuMinute] = field(default_factory=list)
    xids: list[XidRecord] = field(default_factory=list)
    bmc: list[BmcRecord] = field(default_factory=list)


GPU_MINUTES_SQL = """
SELECT time_bucket(INTERVAL '1 minute', time) AS minute, node_id, gpu_index,
       last(gpu_model, time) AS gpu_model,
       count(*) AS samples,
       avg(temperature_c) AS temp_avg, max(temperature_c) AS temp_max,
       avg(power_w) AS power_avg, min(power_limit_w) AS power_limit,
       avg(utilization_pct) AS util_avg, min(sm_clock_mhz) AS clock_min,
       count(*) FILTER (
           WHERE throttle_reasons && ARRAY['hw_thermal_slowdown', 'sw_thermal_slowdown']
       ) AS thermal_throttled,
       count(*) FILTER (WHERE 'sw_power_cap' = ANY(throttle_reasons)) AS power_capped,
       max(ecc_sbe_total) AS sbe, max(ecc_dbe_total) AS dbe,
       max(retired_pages_total) AS retired, max(pcie_replay_total) AS pcie,
       max(nvlink_crc_errors_total) AS nvlink
FROM gpu_metrics
WHERE time >= $1 AND time < $2 AND node_id LIKE $3
GROUP BY 1, 2, 3
"""
XID_SQL = """
SELECT time, node_id, gpu_index, xid_code, message FROM xid_events
WHERE time >= $1 AND time < $2 AND node_id LIKE $3
"""
BMC_SQL = """
SELECT time, node_id, severity, message_id, message, sensor_type, sensor_name, entry_code, reading
FROM bmc_log WHERE time >= $1 AND time < $2 AND node_id LIKE $3
"""


def floor_minute(t: datetime) -> datetime:
    return t.replace(second=0, microsecond=0)


def _batch(by_minute: dict[datetime, MinuteBatch], minute: datetime) -> MinuteBatch:
    return by_minute.setdefault(minute, MinuteBatch(minute))


def _like_prefix(prefix: str) -> str:
    escaped = prefix.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
    return escaped + "%"


async def read_minutes(
    conn: asyncpg.Connection[Any] | asyncpg.pool.PoolConnectionProxy[Any],
    start: datetime,
    end: datetime,
    node_prefix: str = "",
    chunk: timedelta = timedelta(hours=1),
) -> AsyncIterator[MinuteBatch]:
    """Yield one MinuteBatch per minute in [start, end), including empty minutes."""
    pattern = _like_prefix(node_prefix)
    t = floor_minute(start)
    while t < end:
        chunk_end = min(end, t + chunk)
        by_minute: dict[datetime, MinuteBatch] = {}
        for r in await conn.fetch(GPU_MINUTES_SQL, t, chunk_end, pattern):
            _batch(by_minute, r["minute"]).gpus.append(GpuMinute(**dict(r)))
        for r in await conn.fetch(XID_SQL, t, chunk_end, pattern):
            _batch(by_minute, floor_minute(r["time"])).xids.append(
                XidRecord(r["time"], r["node_id"], r["gpu_index"], r["xid_code"], r["message"])
            )
        for r in await conn.fetch(BMC_SQL, t, chunk_end, pattern):
            _batch(by_minute, floor_minute(r["time"])).bmc.append(BmcRecord(**dict(r)))
        m = t
        while m < chunk_end:
            yield by_minute.get(m) or MinuteBatch(m)
            m += MINUTE
        t = chunk_end
