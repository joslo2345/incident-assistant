"""Command line entry point.

Examples:
    uv run replay --duration 10m --speed 10                 # 10 simulated minutes in 1 minute
    uv run replay --duration 1h --speed 0 --concurrency 8   # as fast as possible (throughput)
"""

import argparse
import asyncio
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path

import httpx2

from replayer.fleet import Fleet
from replayer.replay import API_KEY_HEADER, replay
from replayer.synth import FleetSimulator
from replayer.workload import Workload

PACKAGE_DIR = Path(__file__).resolve().parents[2]
_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86_400}


def parse_duration(text: str) -> float:
    """'90', '90s', '10m', '24h', '2d' -> seconds."""
    match = re.fullmatch(r"(\d+(?:\.\d+)?)([smhd]?)", text.strip())
    if not match:
        raise argparse.ArgumentTypeError(f"invalid duration: {text!r}")
    return float(match[1]) * _UNITS[match[2] or "s"]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Replay GPU telemetry into the ingest API.")
    p.add_argument("--api-url", default=os.environ.get("REPLAY_API_URL", "http://localhost:8000"))
    p.add_argument("--api-key", default=os.environ.get("REPLAY_API_KEY", "dev-key"))
    p.add_argument("--duration", type=parse_duration, default=parse_duration("10m"))
    p.add_argument("--speed", type=float, default=1.0, help="1-100x real time; 0 = max")
    p.add_argument("--offset", type=parse_duration, default=0.0, help="Start point in the slice")
    p.add_argument("--batch-size", type=int, default=500)
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--fleet", type=Path, default=PACKAGE_DIR / "fleet.toml")
    p.add_argument("--slice", type=Path, default=PACKAGE_DIR / "data" / "alibaba_slice.csv")
    return p


async def run(args: argparse.Namespace) -> dict[str, float]:
    sim = FleetSimulator(
        Fleet.load(args.fleet),
        Workload.load(args.slice),
        start=datetime.now(UTC).replace(microsecond=0),
        seed=args.seed,
        offset_s=args.offset,
    )
    async with httpx2.AsyncClient(
        base_url=args.api_url, headers={API_KEY_HEADER: args.api_key}, timeout=30
    ) as client:
        stats = await replay(
            sim, client, args.duration, args.speed, args.batch_size, args.concurrency
        )
    return stats.as_dict()


def main() -> None:
    args = build_parser().parse_args()
    if not (args.speed == 0 or 1 <= args.speed <= 100):
        raise SystemExit("--speed must be 0 (max) or between 1 and 100")
    print(json.dumps(asyncio.run(run(args)), indent=2))
