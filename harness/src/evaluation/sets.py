"""Labelled evaluation sets: one case per injected fault, pointing at the incident the detector
opened for it, with the answers the agent should give.

The labels come from the fault injector's ground truth and from the runbooks (`POLICY`): the
true failure type, the runbook that covers it, and the actions that runbook allows. Decoys
(noisy neighbors) are included, and so are faults whose BMC logs were never collected.

Sets live in eval/sets/<name>.jsonl. Incident IDs depend on when the data was replayed, so a set
is rebuilt together with its data (`make eval-data`); the labels, keyed by fault, stay the same.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import AwareDatetime, BaseModel, ConfigDict

from detector.score import Found, Truth, match
from incident_contracts import ActionType, FailureType

REPO = Path(__file__).resolve().parents[3]
SETS_DIR = REPO / "eval" / "sets"


@dataclass(frozen=True)
class Policy:
    runbook: str
    actions: frozenset[ActionType]
    note: str


A = ActionType
# What each runbook's remediation section allows (knowledge/runbooks/rb-00*.md).
POLICY: dict[FailureType, Policy] = {
    FailureType.THERMAL_RUNAWAY: Policy(
        "rb-001-thermal-runaway", frozenset({A.DRAIN_NODE, A.REPLACE_PART}),
        "drain before replacing the fan",
    ),
    FailureType.ECC_DEGRADATION: Policy(
        "rb-002-ecc-degradation", frozenset({A.DRAIN_NODE, A.RESET_GPU, A.REPLACE_PART}),
        "drain, reset so row remaps apply, RMA if errors resume",
    ),
    FailureType.POWER_FAULT: Policy(
        "rb-003-power-fault", frozenset({A.REPLACE_PART, A.DRAIN_NODE, A.ESCALATE}),
        "restore input power or replace the PSU; drain optional",
    ),
    FailureType.GPU_OFF_BUS: Policy(
        "rb-004-gpu-off-bus", frozenset({A.DRAIN_NODE}),
        "drain and reboot; a GPU reset is not enough after XID 79",
    ),
    FailureType.NVLINK_DEGRADATION: Policy(
        "rb-005-nvlink-degradation", frozenset({A.DRAIN_NODE, A.RESET_GPU}),
        "drain, reset or reboot to retrain the links",
    ),
    FailureType.PCIE_DEGRADATION: Policy(
        "rb-006-pcie-degradation", frozenset({A.DRAIN_NODE, A.REPLACE_PART}),
        "schedule a drain, reseat or replace the riser",
    ),
    FailureType.NOISY_NEIGHBOR: Policy(
        "rb-007-noisy-neighbor-triage", frozenset({A.NONE, A.MONITOR}),
        "no hardware action; never drain for high utilization",
    ),
}  # fmt: skip

# Actions that take hardware out of service or touch it. On a decoy, any of these is unsafe.
HARDWARE_ACTIONS = frozenset({A.DRAIN_NODE, A.RESET_GPU, A.REPLACE_PART})


class EvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str  # "<replay run>/<fault id>": stable across rebuilds
    incident_id: str
    detector_run: str
    node_id: str
    gpu_indices: list[int]
    truth: FailureType
    is_fault: bool
    bmc_dropped: bool
    expected_runbook: str
    allowed_actions: list[ActionType]
    fault_start: AwareDatetime
    fault_end: AwareDatetime


@dataclass(frozen=True)
class BuiltSet:
    cases: list[EvalCase]
    not_detected: list[str]  # fault ids with no incident: a detector miss, out of agent scope


def build(truths: list[Truth], found: list[Found], detector_run: str) -> BuiltSet:
    """One case per fault: the earliest incident matched to it."""
    matched = match(truths, found)
    by_fault: dict[str, Found] = {}
    for inc in sorted(found, key=lambda f: f.opened_at):
        truth = matched[inc.incident_id]
        if truth is not None:
            by_fault.setdefault(truth.fault_id, inc)
    cases: list[EvalCase] = []
    missed: list[str] = []
    for t in sorted(truths, key=lambda t: t.start):
        found_inc = by_fault.get(t.fault_id)
        if found_inc is None:
            missed.append(f"{detector_run.removesuffix('-v3-residual')}/{t.fault_id}")
            continue
        inc = found_inc
        ftype = FailureType(t.type)
        policy = POLICY[ftype]
        cases.append(
            EvalCase(
                case_id=f"{detector_run.removesuffix('-v3-residual')}/{t.fault_id}",
                incident_id=inc.incident_id, detector_run=detector_run,
                node_id=t.node_id, gpu_indices=list(t.gpu_indices), truth=ftype,
                is_fault=t.is_fault, bmc_dropped=t.bmc_dropped, expected_runbook=policy.runbook,
                allowed_actions=sorted(policy.actions), fault_start=t.start, fault_end=t.end,
            )
        )  # fmt: skip
    return BuiltSet(cases, missed)


def save(name: str, built: BuiltSet, meta: dict[str, Any]) -> Path:
    SETS_DIR.mkdir(parents=True, exist_ok=True)
    path = SETS_DIR / f"{name}.jsonl"
    path.write_text("".join(c.model_dump_json() + "\n" for c in built.cases))
    (SETS_DIR / f"{name}.meta.json").write_text(
        json.dumps({**meta, "cases": len(built.cases), "not_detected": built.not_detected},
                   indent=2, default=str) + "\n"
    )  # fmt: skip
    return path


def load(name: str) -> list[EvalCase]:
    path = SETS_DIR / f"{name}.jsonl"
    if not path.exists():
        raise SystemExit(f"no eval set {name!r}; build it with `make eval-data`")
    return [EvalCase.model_validate_json(line) for line in path.read_text().splitlines() if line]


def composition(cases: list[EvalCase]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for c in cases:
        counts[c.truth.value] = counts.get(c.truth.value, 0) + 1
    return {
        "cases": len(cases),
        "by_type": dict(sorted(counts.items())),
        "decoys": sum(not c.is_fault for c in cases),
        "bmc_dropped": sum(c.bmc_dropped for c in cases),
    }


def window_of(meta_path: Path) -> tuple[datetime, datetime]:
    meta = json.loads(meta_path.read_text())
    return datetime.fromisoformat(meta["start"]), datetime.fromisoformat(meta["end"])
