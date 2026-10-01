"""Per-GPU load over time, read from a workload slice (see prepare.py)."""

import bisect
import csv
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

Interval = tuple[float, float, float, float]  # (start_s, end_s, util_pct, mem_gib)


@dataclass
class GpuLoad:
    """Step function of total utilization and memory on one GPU.

    Built from intervals in which instances ran. Utilization of overlapping instances is summed
    (the synthesizer caps it at 100%).
    """

    times: list[float] = field(default_factory=list)
    util_pct: list[float] = field(default_factory=list)
    mem_gib: list[float] = field(default_factory=list)

    @classmethod
    def from_intervals(cls, intervals: list[Interval]) -> "GpuLoad":
        deltas: dict[float, list[float]] = defaultdict(lambda: [0.0, 0.0])
        for start, end, util, mem in intervals:
            deltas[start][0] += util
            deltas[start][1] += mem
            deltas[end][0] -= util
            deltas[end][1] -= mem
        load = cls()
        util = mem = 0.0
        for t in sorted(deltas):
            util += deltas[t][0]
            mem += deltas[t][1]
            # Clamp float drift when everything has ended.
            load.times.append(t)
            load.util_pct.append(max(0.0, round(util, 6)))
            load.mem_gib.append(max(0.0, round(mem, 6)))
        return load

    def at(self, t_s: float) -> tuple[float, float]:
        """(util_pct, mem_gib) in effect at time t_s."""
        i = bisect.bisect_right(self.times, t_s) - 1
        if i < 0:
            return 0.0, 0.0
        return self.util_pct[i], self.mem_gib[i]


@dataclass(frozen=True)
class Workload:
    loads: dict[tuple[int, int], GpuLoad]  # (slice node, gpu) -> load
    duration_s: float

    @classmethod
    def load(cls, path: Path) -> "Workload":
        intervals: dict[tuple[int, int], list[Interval]] = defaultdict(list)
        end = 0.0
        with path.open(newline="") as f:
            for row in csv.DictReader(f):
                key = (int(row["node"]), int(row["gpu"]))
                start_s, end_s = float(row["start_s"]), float(row["end_s"])
                intervals[key].append(
                    (start_s, end_s, float(row["util_pct"]), float(row["mem_gib"]))
                )
                end = max(end, end_s)
        loads = {key: GpuLoad.from_intervals(ivs) for key, ivs in intervals.items()}
        return cls(loads=loads, duration_s=end)

    def for_gpu(self, node: int, gpu: int) -> GpuLoad:
        """Load for a GPU. GPUs that ran nothing in the slice are idle."""
        return self.loads.get((node, gpu), GpuLoad())
