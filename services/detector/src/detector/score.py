"""Scores detector incidents against injected-fault ground truth.

Matching: an incident matches a fault on the same node (and an overlapping GPU, for GPU-level
faults) if its signals overlap [fault start - 5 min, fault end + 15 min]. Each incident matches at
most one fault (the nearest start).

- Alarms are incidents not labelled noisy_neighbor.
- Recall per type: share of real faults with at least one matching alarm.
- Time to detect: earliest matching alarm's opened_at (first actionable alert) minus fault start.
- Classification: the earliest matching alarm's type equals the fault's type.
- Precision: alarms that match a real fault / all alarms.
- Decoys (noisy neighbor): an alarm on one is a false positive; a noisy_neighbor label is correct.
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import asyncpg

BEFORE = timedelta(minutes=5)
AFTER = timedelta(minutes=15)


@dataclass(frozen=True)
class Truth:
    fault_id: str
    type: str
    is_fault: bool
    scope: str
    node_id: str
    gpu_indices: tuple[int, ...]
    start: datetime
    end: datetime
    bmc_dropped: bool = False

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> Truth:
        return cls(
            fault_id=d["fault_id"],
            type=d["type"],
            is_fault=d["is_fault"],
            scope=d["scope"],
            node_id=d["node_id"],
            gpu_indices=tuple(d["gpu_indices"]),
            start=datetime.fromisoformat(d["start"]),
            end=datetime.fromisoformat(d["end"]),
            bmc_dropped=d.get("bmc_dropped", False),
        )


@dataclass(frozen=True)
class Found:
    incident_id: str
    node_id: str
    gpus: tuple[int, ...]
    type: str
    severity: str
    opened_at: datetime
    first_signal_at: datetime
    last_signal_at: datetime

    @property
    def alarm(self) -> bool:
        return self.type != "noisy_neighbor"


@dataclass
class TypeScore:
    faults: int = 0
    detected: int = 0
    classified: int = 0
    ttd_s: list[float] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        ttd = sorted(self.ttd_s)
        return {
            "faults": self.faults,
            "detected": self.detected,
            "recall": round(self.detected / self.faults, 3) if self.faults else None,
            "classified_correctly": self.classified,
            "ttd_median_s": round(statistics.median(ttd)) if ttd else None,
            "ttd_max_s": round(ttd[-1]) if ttd else None,
        }


def match(truths: list[Truth], found: list[Found]) -> dict[str, Truth | None]:
    """incident_id -> matched fault (or None)."""
    out: dict[str, Truth | None] = {}
    for inc in found:
        candidates = [
            t
            for t in truths
            if t.node_id == inc.node_id
            and inc.first_signal_at <= t.end + AFTER
            and inc.last_signal_at >= t.start - BEFORE
            and (t.scope == "node" or not inc.gpus or set(inc.gpus) & set(t.gpu_indices))
        ]
        out[inc.incident_id] = min(
            candidates,
            key=lambda t: abs((inc.first_signal_at - t.start).total_seconds()),
            default=None,
        )
    return out


def score(truths: list[Truth], found: list[Found]) -> dict[str, Any]:
    matched = match(truths, found)
    by_fault: dict[str, list[Found]] = defaultdict(list)
    for inc in found:
        if (t := matched[inc.incident_id]) is not None:
            by_fault[t.fault_id].append(inc)

    per_type: dict[str, TypeScore] = defaultdict(TypeScore)
    decoys = {"decoys": 0, "raised_alarm": 0, "labelled_noisy_neighbor": 0, "ignored": 0}
    missed = []
    for t in truths:
        incs = sorted(by_fault[t.fault_id], key=lambda i: i.opened_at)
        alarms = [i for i in incs if i.alarm]
        if not t.is_fault:
            decoys["decoys"] += 1
            if alarms:
                decoys["raised_alarm"] += 1
            elif incs:
                decoys["labelled_noisy_neighbor"] += 1
            else:
                decoys["ignored"] += 1
            continue
        s = per_type[t.type]
        s.faults += 1
        if not alarms:
            missed.append({"fault_id": t.fault_id, "type": t.type, "bmc_dropped": t.bmc_dropped})
            continue
        s.detected += 1
        s.ttd_s.append((alarms[0].opened_at - t.start).total_seconds())
        s.classified += alarms[0].type == t.type

    alarms = [i for i in found if i.alarm]
    true_alarms = [i for i in alarms if (t := matched[i.incident_id]) is not None and t.is_fault]
    false_alarms = [i for i in alarms if i not in true_alarms]
    total = TypeScore()
    for s in per_type.values():
        total.faults += s.faults
        total.detected += s.detected
        total.classified += s.classified
        total.ttd_s += s.ttd_s
    fa_types: dict[str, int] = defaultdict(int)
    for i in false_alarms:
        fa_types[i.type] += 1
    return {
        "per_type": {k: v.summary() for k, v in sorted(per_type.items())},
        "overall": {
            **total.summary(),
            "alarms": len(alarms),
            "precision": round(len(true_alarms) / len(alarms), 3) if alarms else None,
            "false_alarms": len(false_alarms),
            "false_alarms_by_type": dict(sorted(fa_types.items())),
        },
        "decoys": decoys,
        "missed": missed,
    }


def to_markdown(result: dict[str, Any], title: str) -> str:
    lines = [
        f"### {title}",
        "",
        "| Failure type | Faults | Recall | Classified | TTD median | TTD max |",
        "| --- | --- | --- | --- | --- | --- |",
    ]

    def fmt_s(s: float | None) -> str:
        return "-" if s is None else f"{s / 60:.1f} min"

    for name, s in [*result["per_type"].items(), ("**all faults**", result["overall"])]:
        recall = "-" if s["recall"] is None else f"{s['recall']:.0%}"
        lines.append(
            f"| {name} | {s['faults']} | {recall} | {s['classified_correctly']}/{s['detected']} | "
            f"{fmt_s(s['ttd_median_s'])} | {fmt_s(s['ttd_max_s'])} |"
        )
    o, d = result["overall"], result["decoys"]
    precision = "-" if o["precision"] is None else f"{o['precision']:.0%}"
    lines += [
        "",
        f"Precision {precision} ({o['alarms'] - o['false_alarms']}/{o['alarms']} alarms match a "
        f"real fault); false alarms by type: {o['false_alarms_by_type'] or 'none'}.",
        f"Noisy-neighbor decoys: {d['decoys']}; raised an alarm {d['raised_alarm']}, labelled "
        f"noisy_neighbor {d['labelled_noisy_neighbor']}, ignored {d['ignored']}.",
    ]
    if result["missed"]:
        lines.append(f"Missed: {', '.join(m['fault_id'] for m in result['missed'])}.")
    return "\n".join(lines) + "\n"


def load_truth(path: Path) -> list[Truth]:
    return [Truth.from_json(json.loads(line)) for line in path.read_text().splitlines() if line]


async def load_found(database_url: str, run: str) -> list[Found]:
    conn = await asyncpg.connect(database_url)
    try:
        rows = await conn.fetch(
            "SELECT incident_id::text, node_id, gpu_indices, suspected_failure_type, severity, "
            "opened_at, first_signal_at, last_signal_at FROM incidents WHERE detector_run = $1",
            run,
        )
    finally:
        await conn.close()
    return [
        Found(
            r["incident_id"],
            r["node_id"],
            tuple(r["gpu_indices"]),
            r["suspected_failure_type"],
            r["severity"],
            r["opened_at"],
            r["first_signal_at"],
            r["last_signal_at"],
        )
        for r in rows
    ]


async def score_main(args: argparse.Namespace) -> None:
    result = score(
        load_truth(Path(args.ground_truth)), await load_found(args.database_url, args.run)
    )
    if args.json:
        Path(args.json).write_text(json.dumps(result, indent=2, default=str) + "\n")
    print(to_markdown(result, f"Detector run `{args.run}`"))


__all__ = ["Found", "Truth", "score", "to_markdown"]
