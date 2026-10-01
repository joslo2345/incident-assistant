"""Detector CLI.

    detect live                       # every minute, process the minute that just completed
    detect backfill --from 2026-09-29T00:00Z --to 2026-09-30T00:00Z --node-prefix ev1- \\
        --run ev1-v3 --layers static,zscore,residual
    detect score --run ev1-v3 --ground-truth eval/runs/ev1/ground_truth.jsonl
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import signal
import time
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
from prometheus_client import Counter, Gauge, start_http_server

from detector.engine import Detector
from detector.features import floor_minute, read_minutes
from detector.incidents import OpenIncident
from detector.rules import Config
from detector.store import clear_run, save

log = logging.getLogger("detector")

INCIDENTS = Counter("detector_incident_updates_total", "Incident upserts", ["type", "status"])
LAG = Gauge("detector_lag_seconds", "How far behind real time the last processed minute is")
MINUTES = Counter("detector_minutes_total", "Minutes processed")

DEFAULT_DB = "postgresql://incident_detector@localhost:5432/telemetry"
LAYERS = ("static", "zscore", "residual")


def config_for(layers: str) -> Config:
    chosen = {name.strip() for name in layers.split(",") if name.strip()}
    if unknown := chosen - set(LAYERS):
        raise SystemExit(f"unknown layers: {sorted(unknown)}; choose from {LAYERS}")
    return Config(
        static="static" in chosen, zscore="zscore" in chosen, residual="residual" in chosen
    )


def parse_time(text: str) -> datetime:
    t = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if t.tzinfo is None:
        raise SystemExit(f"time needs a timezone: {text}")
    return t


async def backfill(args: argparse.Namespace) -> dict[str, object]:
    started = time.monotonic()
    detector = Detector(config_for(args.layers), args.run)
    conn = await asyncpg.connect(args.database_url)
    try:
        await clear_run(conn, args.run)
        latest: dict[uuid.UUID, OpenIncident] = {}
        minutes = 0
        async for batch in read_minutes(conn, args.start, args.end, args.node_prefix):
            for inc in detector.process(batch):
                latest[inc.incident_id] = inc
            minutes += 1
        for inc in detector.tracker.close_all(args.end):
            latest[inc.incident_id] = inc
        written = await save(conn, args.run, latest.values())
    finally:
        await conn.close()
    return {
        "run": args.run,
        "layers": args.layers,
        "minutes": minutes,
        "alerts": detector.alerts_total,
        "incidents": written,
        "seconds": round(time.monotonic() - started, 1),
    }


async def live(args: argparse.Namespace, stop: asyncio.Event) -> None:
    """Process each completed minute, `delay` behind real time so late telemetry has arrived."""
    detector = Detector(config_for(args.layers), "live")
    pool = await asyncpg.create_pool(args.database_url, min_size=1, max_size=2)
    delay = timedelta(seconds=args.delay)
    # Warm up baselines on recent history; incident IDs are deterministic, so re-writing is safe.
    next_minute = floor_minute(datetime.now(UTC) - delay) - timedelta(hours=args.warmup_hours)
    try:
        while not stop.is_set():
            ready_until = floor_minute(datetime.now(UTC) - delay)
            if next_minute < ready_until:
                async with pool.acquire() as conn:
                    changed: dict[uuid.UUID, OpenIncident] = {}
                    async for batch in read_minutes(conn, next_minute, ready_until):
                        for inc in detector.process(batch):
                            changed[inc.incident_id] = inc
                        MINUTES.inc()
                    await save(conn, "live", changed.values())
                for inc in changed.values():
                    INCIDENTS.labels(inc.failure_type.value, inc.status.value).inc()
                next_minute = ready_until
                LAG.set((datetime.now(UTC) - next_minute).total_seconds())
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=10)
    finally:
        await pool.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL", DEFAULT_DB))
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_live = sub.add_parser("live", help="Run continuously")
    p_live.add_argument("--layers", default=",".join(LAYERS))
    p_live.add_argument("--delay", type=int, default=120, help="Seconds behind real time")
    p_live.add_argument("--warmup-hours", type=float, default=3)
    p_live.add_argument("--metrics-port", type=int, default=9102)

    p_back = sub.add_parser("backfill", help="Process a past time range")
    p_back.add_argument("--from", dest="start", type=parse_time, required=True)
    p_back.add_argument("--to", dest="end", type=parse_time, required=True)
    p_back.add_argument("--node-prefix", default="")
    p_back.add_argument("--run", required=True, help="detector_run label for the incidents")
    p_back.add_argument("--layers", default=",".join(LAYERS))

    p_score = sub.add_parser("score", help="Score a run against ground truth")
    p_score.add_argument("--run", required=True)
    p_score.add_argument("--ground-truth", required=True)
    p_score.add_argument("--json", help="Also write the full result to this file")

    args = parser.parse_args()
    if args.cmd == "backfill":
        print(json.dumps(asyncio.run(backfill(args)), indent=2))
    elif args.cmd == "score":
        from detector.score import score_main

        asyncio.run(score_main(args))
    else:
        start_http_server(args.metrics_port)

        async def _run() -> None:
            stop = asyncio.Event()
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, stop.set)
            await live(args, stop)

        asyncio.run(_run())
