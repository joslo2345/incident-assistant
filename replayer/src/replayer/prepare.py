"""Build a small workload slice from the raw Alibaba cluster-trace-gpu-v2020.

The trace has no GPU time series. For each job instance it records the machine, the GPU device it
ran on (/dev/nvidiaN), start and end times, and lifetime-average GPU utilization and memory. This
script picks the busiest day, then the 8-GPU machines that day that are busy (high average
utilization) and have the most jobs starting and stopping, and writes one row per
(instance, GPU) with times relative to the window start. The synthesizer adds up the rows on each
GPU to get utilization over time.

Usage (after scripts/download_alibaba_trace.sh):
    uv run replay-prepare --data-dir data/alibaba --out replayer/data
"""

import argparse
import json
import math
from pathlib import Path

import polars as pl

GPUS_PER_MACHINE = 8
DAY_S = 86_400
SLICE_COLUMNS = ["node", "gpu", "start_s", "end_s", "util_pct", "mem_gib"]


def _scan(data_dir: Path, table: str) -> pl.LazyFrame:
    columns = (data_dir / f"{table}.header").read_text().strip().split(",")
    return pl.scan_csv(
        data_dir / f"{table}.csv", has_header=False, new_columns=columns, infer_schema_length=10_000
    )


def load_instances(data_dir: Path) -> pl.DataFrame:
    """Instances on 8-GPU machines, joined with their GPU sensor averages."""
    spec = _scan(data_dir, "pai_machine_spec").filter(pl.col("cap_gpu") == GPUS_PER_MACHINE)
    sensors = _scan(data_dir, "pai_sensor_table").select(
        "worker_name", "gpu_name", "gpu_wrk_util", "avg_gpu_wrk_mem"
    )
    return (
        _scan(data_dir, "pai_instance_table")
        .filter(pl.col("start_time").is_not_null() & pl.col("end_time").is_not_null())
        .join(spec.select("machine"), on="machine", how="semi")
        .join(sensors, on="worker_name", how="inner")
        .filter(pl.col("gpu_name").str.starts_with("/dev/nvidia"))
        .filter((pl.col("gpu_wrk_util") > 0) | (pl.col("avg_gpu_wrk_mem") > 0))
        .select(
            "machine",
            pl.col("gpu_name").str.extract(r"(\d+)$").cast(pl.Int32).alias("gpu"),
            pl.col("start_time").cast(pl.Float64).alias("start"),
            pl.col("end_time").cast(pl.Float64).alias("end"),
            pl.col("gpu_wrk_util").alias("util_pct"),
            pl.col("avg_gpu_wrk_mem").alias("mem_gib"),
        )
        .collect()
    )


def _with_overlap(df: pl.DataFrame, start: float, end: float) -> pl.DataFrame:
    overlap = pl.min_horizontal(pl.col("end"), pl.lit(end)) - pl.max_horizontal(
        pl.col("start"), pl.lit(start)
    )
    return df.with_columns(gpu_s=overlap.clip(lower_bound=0) * pl.col("util_pct") / 100)


def busiest_day(df: pl.DataFrame) -> int:
    first = int(df.select(pl.col("start").min() // DAY_S).item())
    last = int(df.select(pl.col("end").max() // DAY_S).item())
    totals = {
        day: _with_overlap(df, day * DAY_S, (day + 1) * DAY_S)["gpu_s"].sum()
        for day in range(first, last + 1)
    }
    return max(totals, key=lambda d: totals[d])


def build_slice(
    df: pl.DataFrame, window_start: float, window_s: int, n_machines: int, min_util_pct: float
) -> tuple[pl.DataFrame, list[str]]:
    window_end = window_start + window_s
    in_window = _with_overlap(df, window_start, window_end).filter(pl.col("gpu_s") > 0)
    # Ranking by GPU-seconds alone picks machines running one 8-GPU job all day, which gives flat
    # telemetry. Instead, among busy machines, prefer the ones with the most job churn.
    capacity_s = GPUS_PER_MACHINE * window_s
    machines = (
        in_window.group_by("machine")
        .agg((pl.col("gpu_s").sum() / capacity_s * 100).alias("mean_util"), pl.len().alias("jobs"))
        .filter(pl.col("mean_util") >= min_util_pct)
        .sort(["jobs", "machine"], descending=[True, False])
        .head(n_machines)["machine"]
        .to_list()
    )
    if len(machines) < n_machines:
        raise SystemExit(f"only {len(machines)} machines reach {min_util_pct}% mean utilization")
    node_of = {m: i for i, m in enumerate(machines)}

    rows = []
    for r in in_window.filter(pl.col("machine").is_in(machines)).iter_rows(named=True):
        # util > 100 means the instance used several GPUs; the trace names only the first.
        n_gpus = min(GPUS_PER_MACHINE, max(1, math.ceil(r["util_pct"] / 100)))
        for k in range(n_gpus):
            rows.append(
                {
                    "node": node_of[r["machine"]],
                    "gpu": (r["gpu"] + k) % GPUS_PER_MACHINE,
                    "start_s": max(r["start"], window_start) - window_start,
                    "end_s": min(r["end"], window_end) - window_start,
                    "util_pct": r["util_pct"] / n_gpus,
                    "mem_gib": r["mem_gib"] / n_gpus,
                }
            )
    out = (
        pl.DataFrame(rows, schema=dict.fromkeys(SLICE_COLUMNS, pl.Float64))
        .with_columns(
            pl.col("node", "gpu").cast(pl.Int32),
            pl.col("start_s", "end_s").round(0).cast(pl.Int64),
            pl.col("util_pct", "mem_gib").round(3),
        )
        .filter(pl.col("end_s") > pl.col("start_s"))
        .sort(["start_s", "node", "gpu"])
    )
    return out, machines


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-dir", type=Path, default=Path("data/alibaba"))
    parser.add_argument("--out", type=Path, default=Path("replayer/data"))
    parser.add_argument("--machines", type=int, default=8)
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--day", type=int, help="Trace day to use (default: busiest)")
    parser.add_argument("--min-util", type=float, default=40.0, help="Min mean GPU util, %%")
    args = parser.parse_args()

    df = load_instances(args.data_dir)
    day = args.day if args.day is not None else busiest_day(df)
    window_s = args.hours * 3600
    slice_df, machines = build_slice(df, day * DAY_S, window_s, args.machines, args.min_util)

    args.out.mkdir(parents=True, exist_ok=True)
    slice_df.write_csv(args.out / "alibaba_slice.csv")
    meta = {
        "source": "Alibaba cluster-trace-gpu-v2020 (pai_instance_table, pai_sensor_table, "
        "pai_machine_spec)",
        "source_url": "https://github.com/alibaba/clusterdata/tree/master/cluster-trace-gpu-v2020",
        "license": "CC BY 4.0. Derived data: rows filtered, times shifted, multi-GPU "
        "instances split across GPUs.",
        "trace_day": day,
        "min_mean_util_pct": args.min_util,
        "window_s": window_s,
        "machines": machines,
        "rows": slice_df.height,
        "columns": {
            "node": "index into the selected machines (0-based)",
            "gpu": "GPU index on the machine",
            "start_s": "instance start, seconds from window start",
            "end_s": "instance end, seconds from window start",
            "util_pct": "lifetime-average GPU utilization this instance put on this GPU",
            "mem_gib": "lifetime-average GPU memory this instance used on this GPU",
        },
    }
    (args.out / "alibaba_slice.meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    print(f"day {day}: {slice_df.height} rows from {len(machines)} machines -> {args.out}")
