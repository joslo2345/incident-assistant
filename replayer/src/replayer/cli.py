"""Command line entry point.

Examples:
    uv run replay --duration 10m --speed 10                 # 10 simulated minutes in 1 minute
    uv run replay --duration 1h --speed 0 --concurrency 8   # as fast as possible (throughput)
    uv run replay --duration 3d --speed 0 --start now-3d    # backfill the last 3 days
    uv run replay --duration 1d --speed 0 --start now-2d --run-id ev1 --faults 14
        # 14 injected faults on nodes named ev1-*, ground truth in eval/runs/ev1/
"""

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx2

from incident_contracts import FailureType
from replayer.faults import plan_faults, write_ground_truth
from replayer.fleet import Fleet
from replayer.replay import API_KEY_HEADER, replay
from replayer.synth import FleetSimulator
from replayer.workload import Workload

PACKAGE_DIR = Path(__file__).resolve().parents[2]
REPO_DIR = PACKAGE_DIR.parent
_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86_400}


def parse_duration(text: str) -> float:
    """'90', '90s', '10m', '24h', '2d' -> seconds."""
    match = re.fullmatch(r"(\d+(?:\.\d+)?)([smhd]?)", text.strip())
    if not match:
        raise argparse.ArgumentTypeError(f"invalid duration: {text!r}")
    return float(match[1]) * _UNITS[match[2] or "s"]


def parse_start(text: str) -> datetime:
    """'now', 'now-3d' (relative to now), or an ISO 8601 time with a timezone."""
    now = datetime.now(UTC).replace(microsecond=0)
    if text == "now":
        return now
    if text.startswith("now-"):
        return now - timedelta(seconds=parse_duration(text.removeprefix("now-")))
    try:
        start = datetime.fromisoformat(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid start: {text!r}") from exc
    if start.tzinfo is None:
        raise argparse.ArgumentTypeError("start time needs a timezone, e.g. 2026-10-01T00:00Z")
    return start


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Replay GPU telemetry into the ingest API.")
    p.add_argument("--api-url", default=os.environ.get("REPLAY_API_URL", "http://localhost:8000"))
    p.add_argument("--api-key", default=os.environ.get("REPLAY_API_KEY", "dev-key"))
    p.add_argument("--duration", type=parse_duration, default=parse_duration("10m"))
    p.add_argument("--speed", type=float, default=1.0, help="1-100x real time; 0 = max")
    p.add_argument("--offset", type=parse_duration, default=0.0, help="Start point in the slice")
    p.add_argument(
        "--start",
        type=parse_start,
        default="now",
        help="Timestamp of the first sample: now, -3d, ISO",
    )
    p.add_argument("--batch-size", type=int, default=500)
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--fleet", type=Path, default=PACKAGE_DIR / "fleet.toml")
    p.add_argument("--slice", type=Path, default=PACKAGE_DIR / "data" / "alibaba_slice.csv")
    p.add_argument(
        "--run-id",
        type=_run_id,
        default="",
        help="Prefix node IDs with '<run-id>-' so this run's data can be told apart",
    )
    p.add_argument("--faults", type=int, default=0, help="Number of faults to inject")
    p.add_argument(
        "--fault-types",
        type=lambda s: [FailureType(t) for t in s.split(",")],
        default=None,
        help="Comma-separated failure types to cycle through (default: all 7)",
    )
    p.add_argument(
        "--bmc-dropout",
        type=float,
        default=0.0,
        help="Share of faults whose BMC entries are never collected (0-1)",
    )
    p.add_argument(
        "--ground-truth",
        type=Path,
        default=None,
        help="Where to write injected faults (default: eval/runs/<run-id>/ground_truth.jsonl)",
    )
    return p


def _run_id(text: str) -> str:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,15}", text):
        raise argparse.ArgumentTypeError("run id: lowercase letters, digits, dashes; max 16")
    return text


async def run(args: argparse.Namespace) -> dict[str, float]:
    fleet = Fleet.load(args.fleet)
    prefix = f"{args.run_id}-" if args.run_id else ""
    faults = []
    if args.faults:
        nodes = [(prefix + n.node_id, n.gpus) for n in fleet.nodes]
        types = args.fault_types or list(FailureType)[:-1]  # every type except UNKNOWN
        faults = plan_faults(
            nodes, args.duration, args.faults, args.seed, types, bmc_dropout=args.bmc_dropout
        )
        gt = args.ground_truth or REPO_DIR / "eval" / "runs" / (args.run_id or "default") / (
            "ground_truth.jsonl"
        )
        write_ground_truth(gt, faults, args.start)
        print(f"injecting {len(faults)} faults; ground truth: {gt}", file=sys.stderr)
    sim = FleetSimulator(
        fleet,
        Workload.load(args.slice),
        start=args.start,
        seed=args.seed,
        offset_s=args.offset,
        node_prefix=prefix,
        faults=faults,
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
