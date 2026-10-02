"""End-to-end detector evaluation against injected faults.

1. Replays `--duration` of telemetry with `--faults` injected faults into the running stack, on
   nodes prefixed with the run id (so runs never mix with other data).
2. Waits until the consumer has written everything.
3. Runs the detector over that time range once per layer configuration.
4. Scores each run against the ground truth and writes eval/runs/<run-id>/report.md.

Usage (stack running):  uv run python scripts/eval_detection.py --run-id ev1 --duration 2d
"""

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import asyncpg

from detector.main import backfill
from detector.score import load_found, load_truth, score, to_markdown

REPO = Path(__file__).resolve().parent.parent
CONFIGS = [
    ("v1-static", "static", "Static thresholds + event rules"),
    ("v2-zscore", "static,zscore", "+ per-GPU rolling z-scores"),
    ("v3-residual", "static,zscore,residual", "+ thermal residual model (temp vs power)"),
]
_UNITS = {"m": 60, "h": 3600, "d": 86_400}


def duration_s(text: str) -> int:
    return int(float(text[:-1]) * _UNITS[text[-1]]) if text[-1] in _UNITS else int(text)


def database_url() -> str:
    if url := os.environ.get("DETECTOR_DATABASE_URL"):
        return url
    env = dict(
        line.split("=", 1)
        for line in (REPO / "deploy" / ".env").read_text().splitlines()
        if "=" in line and not line.startswith("#")
    )
    return f"postgresql://incident_detector:{env['DETECTOR_DB_PASSWORD']}@localhost:5432/telemetry"


async def wait_for_rows(url: str, prefix: str, expected: int, timeout_s: float = 600) -> int:
    conn = await asyncpg.connect(url)
    pattern = prefix.replace("_", r"\_") + "%"
    query = (
        "SELECT (SELECT count(*) FROM gpu_metrics WHERE node_id LIKE $1) + "
        "(SELECT count(*) FROM xid_events WHERE node_id LIKE $1) + "
        "(SELECT count(*) FROM bmc_log WHERE node_id LIKE $1)"
    )
    deadline = time.monotonic() + timeout_s
    try:
        while True:
            rows = int(await conn.fetchval(query, pattern))
            if rows >= expected or time.monotonic() > deadline:
                return rows
            await asyncio.sleep(2)
    finally:
        await conn.close()


async def wait_for_rollup(
    url: str, prefix: str, start: datetime, end: datetime, timeout_s: float = 300
) -> bool:
    """Wait until the 1-minute rollup covers the replayed window. Backfilled data below the
    rollup's watermark is invisible until the refresh policy (every minute) materializes it, and
    the agent's get_metrics reads the rollup."""
    conn = await asyncpg.connect(url)
    pattern = prefix.replace("_", r"\_") + "%"
    deadline = time.monotonic() + timeout_s
    try:
        while time.monotonic() < deadline:
            row = await conn.fetchrow(
                "SELECT min(bucket) AS first, max(bucket) AS last FROM gpu_metrics_1m "
                "WHERE node_id LIKE $1 AND bucket >= $2 AND bucket < $3", pattern, start, end,
            )  # fmt: skip
            first, last = (row["first"], row["last"]) if row else (None, None)
            if (
                first is not None
                and last is not None
                and first <= start + timedelta(minutes=5)
                and last >= end - timedelta(minutes=5)
            ):
                return True
            await asyncio.sleep(5)
        return False
    finally:
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--duration", default="2d")
    parser.add_argument("--faults", type=int, default=28)
    parser.add_argument("--seed", type=int, default=11)
    parser.add_argument("--bmc-dropout", type=float, default=0.5)
    parser.add_argument("--fault-types", help="comma-separated failure types (default: all)")
    parser.add_argument("--skip-replay", action="store_true", help="Reuse data already replayed")
    args = parser.parse_args()

    run_dir = REPO / "eval" / "runs" / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    meta_path = run_dir / "meta.json"
    url = database_url()
    prefix = f"{args.run_id}-"

    if args.skip_replay:
        meta = json.loads(meta_path.read_text())
    else:
        seconds = duration_s(args.duration)
        # Start on a whole minute: the detector works in 1-minute buckets, so a replay that
        # starts at a different second shifts samples between buckets and yields different
        # alerts. Aligned, the same seed always gives the same incidents (the CI eval relies on it).
        start = (datetime.now(UTC) - timedelta(seconds=seconds + 3600)).replace(
            second=0, microsecond=0
        )
        cmd = [
            "uv", "run", "replay", "--speed", "0", "--duration", str(seconds),
            "--start", start.isoformat(), "--run-id", args.run_id, "--faults", str(args.faults),
            "--seed", str(args.seed), "--bmc-dropout", str(args.bmc_dropout),
            "--ground-truth", str(run_dir / "ground_truth.jsonl"),
        ]  # fmt: skip
        if args.fault_types:
            cmd += ["--fault-types", args.fault_types]
        print(f"replaying {args.duration} with {args.faults} faults...", file=sys.stderr)
        stats = json.loads(
            subprocess.run(cmd, cwd=REPO, check=True, capture_output=True, text=True).stdout
        )
        rows = asyncio.run(wait_for_rows(url, prefix, int(stats["accepted"])))
        if rows < stats["accepted"]:
            raise SystemExit(f"only {rows}/{stats['accepted']} rows arrived; is the consumer up?")
        if not asyncio.run(wait_for_rollup(url, prefix, start, start + timedelta(seconds=seconds))):
            raise SystemExit("the 1-minute rollup did not cover the replayed window in time")
        meta = {
            "run_id": args.run_id, "start": start.isoformat(),
            "end": (start + timedelta(seconds=seconds)).isoformat(), "duration_s": seconds,
            "faults": args.faults, "seed": args.seed, "bmc_dropout": args.bmc_dropout,
            "events": stats["accepted"],
        }  # fmt: skip
        meta_path.write_text(json.dumps(meta, indent=2) + "\n")

    truths = load_truth(run_dir / "ground_truth.jsonl")
    results: dict[str, Any] = {}
    sections = []
    for name, layers, label in CONFIGS:
        det_run = f"{args.run_id}-{name}"
        ns = argparse.Namespace(
            database_url=url, start=datetime.fromisoformat(meta["start"]),
            end=datetime.fromisoformat(meta["end"]), node_prefix=prefix, run=det_run, layers=layers,
        )  # fmt: skip
        info = asyncio.run(backfill(ns))
        result = score(truths, asyncio.run(load_found(url, det_run)))
        results[name] = {"backfill": info, **result}
        sections.append(to_markdown(result, f"{name}: {label}"))
        print(f"{name}: {info['incidents']} incidents in {info['seconds']} s", file=sys.stderr)

    summary = [
        "| Config | Recall | Precision | False alarms | Decoy alarms | TTD median | Classified |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for name, _, label in CONFIGS:
        o, d = results[name]["overall"], results[name]["decoys"]
        ttd = "-" if o["ttd_median_s"] is None else f"{o['ttd_median_s'] / 60:.1f} min"
        prec = "-" if o["precision"] is None else f"{o['precision']:.0%}"
        recall = "-" if o["recall"] is None else f"{o['recall']:.0%}"
        summary.append(
            f"| {name} ({label}) | {recall} | {prec} | {o['false_alarms']} | "
            f"{d['raised_alarm']}/{d['decoys']} | {ttd} | "
            f"{o['classified_correctly']}/{o['detected']} |"
        )
    report = "\n".join([
        f"# Detector evaluation `{args.run_id}`", "",
        f"{meta['duration_s'] / 3600:.0f} h replayed ({meta['events']:,} events), "
        f"{meta['faults']} injected faults, seed {meta['seed']}, BMC dropout "
        f"{meta['bmc_dropout']:.0%}. Window {meta['start']} to {meta['end']}.", "",
        *summary, "", *sections,
    ])  # fmt: skip
    (run_dir / "report.md").write_text(report)
    (run_dir / "results.json").write_text(json.dumps(results, indent=2, default=str) + "\n")
    print(report)


if __name__ == "__main__":
    main()
