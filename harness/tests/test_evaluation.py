import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from agent.llm import ProviderError, ScriptedProvider, ToolCall, Turn, Usage
from detector.score import Found, Truth
from evaluation.calibrate import calibrate, cohen_kappa
from evaluation.cassette import Cassette, RecordingProvider, ReplayProvider, anchor
from evaluation.cli import _check_gate, _gate_values
from evaluation.score import CaseResult, score_case, summarize
from evaluation.sets import POLICY, EvalCase, build
from incident_contracts import (
    ComponentKind,
    ComponentRef,
    DetectorKind,
    Evidence,
    FailureType,
    Incident,
    IncidentStatus,
    Severity,
)

T0 = datetime(2026, 9, 30, 2, 0, tzinfo=UTC)


def truth(fid: str, ftype: str, node: str = "n1", gpus: tuple[int, ...] = (2,),
          start: datetime = T0, is_fault: bool = True) -> Truth:  # fmt: skip
    return Truth(fid, ftype, is_fault, "gpu", node, gpus, start, start + timedelta(minutes=30))


def found(iid: str, ftype: str, node: str = "n1", gpus: tuple[int, ...] = (2,),
          at: datetime = T0, sev: str = "sev2") -> Found:  # fmt: skip
    return Found(iid, node, gpus, ftype, sev, at, at, at + timedelta(minutes=5))


def test_build_takes_the_earliest_incident_per_fault_and_reports_misses() -> None:
    truths = [truth("f1", "pcie_degradation"),
              truth("f2", "noisy_neighbor", node="n2", is_fault=False)]  # fmt: skip
    incidents = [
        found("late", "pcie_degradation", at=T0 + timedelta(minutes=10)),
        found("early", "pcie_degradation", at=T0 + timedelta(minutes=1)),
    ]
    built = build(truths, incidents, "a6x-v3-residual")
    assert [c.incident_id for c in built.cases] == ["early"]
    c = built.cases[0]
    assert c.case_id == "a6x/f1"
    assert c.expected_runbook == "rb-006-pcie-degradation"
    assert set(c.allowed_actions) == {"drain_node", "replace_part"}
    assert built.not_detected == ["a6x/f2"]


def test_policy_covers_every_failure_type_and_matches_runbook_files() -> None:
    runbooks = {p.stem for p in (Path(__file__).parents[2] / "knowledge" / "runbooks").glob("*.md")}
    for ftype in FailureType:
        if ftype is FailureType.UNKNOWN:
            continue
        assert POLICY[ftype].runbook in runbooks, ftype
    # A GPU reset is explicitly not enough after XID 79 (rb-004).
    assert "reset_gpu" not in POLICY[FailureType.GPU_OFF_BUS].actions
    assert POLICY[FailureType.NOISY_NEIGHBOR].actions == {"none", "monitor"}


def case(ftype: str = "thermal_runaway", is_fault: bool = True, bmc: bool = False) -> EvalCase:
    p = POLICY[FailureType(ftype)]
    return EvalCase(
        case_id=f"x/{ftype}", incident_id=str(uuid.uuid4()), detector_run="x-v3-residual",
        node_id="n1", gpu_indices=[0], truth=FailureType(ftype), is_fault=is_fault,
        bmc_dropped=bmc, expected_runbook=p.runbook, allowed_actions=sorted(p.actions),
        fault_start=T0, fault_end=T0 + timedelta(hours=1),
    )  # fmt: skip


def result(root: str | None, action: str | None, cited: list[str] | None = None,
           **kw: Any) -> CaseResult:  # fmt: skip
    base: dict[str, Any] = dict(
        case_id="x", incident_id="i", truth="", is_fault=True, bmc_dropped=False, run_id="r",
        status="succeeded" if root else "invalid_output", root_cause=root, confidence=0.9,
        action=action, cited_chunks=cited or [], summary="s", rationale="r", steps=4,
        tool_calls=3, tool_errors=0, text_tool_calls=0, input_tokens=1000, output_tokens=100,
        cost_usd=0.0, latency_s=100.0,
    )  # fmt: skip
    base.update(kw)
    return CaseResult(**base)


def test_scoring_rules() -> None:
    thermal = case("thermal_runaway")
    ok = score_case(thermal, result("thermal_runaway", "drain_node",
                                    ["rb-001-thermal-runaway#remediation"]))  # fmt: skip
    assert (ok.root_cause_correct, ok.action_correct, ok.runbook_cited) == (True, True, True)
    assert not ok.missed_action and not ok.unsafe_action

    # The A5 failure mode: a real fault called noisy_neighbor and left in service.
    missed = score_case(thermal, result("noisy_neighbor", "monitor",
                                        ["rb-007-noisy-neighbor-triage#what-to-do"]))  # fmt: skip
    assert not missed.root_cause_correct and missed.missed_action and not missed.runbook_cited

    decoy = case("noisy_neighbor", is_fault=False)
    unsafe = score_case(decoy, result("thermal_runaway", "drain_node"))
    assert unsafe.unsafe_action and not unsafe.action_correct
    calm = score_case(decoy, result("noisy_neighbor", "none"))
    assert calm.action_correct and not calm.unsafe_action and not calm.missed_action

    # Right cause, wrong action for the runbook: a GPU reset after XID 79.
    off_bus = score_case(case("gpu_off_bus"), result("gpu_off_bus", "reset_gpu"))
    assert off_bus.root_cause_correct and not off_bus.action_correct

    # No diagnosis: wrong on everything.
    failed = score_case(thermal, result(None, None))
    assert not (failed.root_cause_correct or failed.action_correct or failed.runbook_cited)


def test_summary_rates_use_the_right_denominators() -> None:
    results = [
        score_case(
            case("thermal_runaway", bmc=True), result("noisy_neighbor", "none", bmc_dropped=True)
        ),
        score_case(case("power_fault"), result("power_fault", "replace_part")),
        score_case(
            case("noisy_neighbor", is_fault=False),
            result("thermal_runaway", "drain_node", is_fault=False),
        ),
    ]
    s = summarize(results)
    assert s["root_cause_accuracy"] == {"n": 1, "of": 3, "rate": 0.333}
    assert s["root_cause_accuracy_bmc_dropped"]["of"] == 1
    assert s["unsafe_action_rate"] == {"n": 1, "of": 1, "rate": 1.0}  # decoys only
    assert s["missed_action_rate"] == {"n": 1, "of": 2, "rate": 0.5}  # real faults only


def incident(start: datetime = T0) -> Incident:
    node = ComponentRef(kind=ComponentKind.NODE, node_id="a6ci-h100-node-01")
    return Incident(
        incident_id=uuid.uuid4(), created_at=start, updated_at=start, status=IncidentStatus.OPEN,
        severity=Severity.SEV2, title="t", components=[node],
        evidence=[Evidence(evidence_id="ev-0", detector=DetectorKind.EVENT_MATCH, component=node,
                           signal="s", window_start=start, window_end=start + timedelta(minutes=1),
                           description="d")],
    )  # fmt: skip


async def test_recording_replays_against_shifted_data() -> None:
    recorded_incident = incident(T0)
    call = ToolCall("c1", "get_logs", {"node_id": "a6ci-h100-node-01",
                                       "start": "2026-09-30T01:30:00Z",
                                       "end": "2026-09-30T02:15:00Z"})  # fmt: skip
    sim = ToolCall(
        "c2", "find_similar_incidents", {"incident_id": str(recorded_incident.incident_id)}
    )
    turns = [Turn("", [call, sim], Usage(900, 80), "tool_use", "qwen3-agent")]
    cassette = Cassette(recorded_with={"model": "qwen3-agent"})
    rec = RecordingProvider(ScriptedProvider(list(turns)), cassette)
    rec.begin("a6ci/f1", recorded_incident)
    await rec.start("sys", "user").next([])
    stored = json.dumps(cassette.cases["f1"])  # keyed by fault, not run
    assert "{{t-1800}}" in stored and "{{t+900}}" in stored and "{{incident_id}}" in stored
    assert "{{run}}-h100-node-01" in stored

    # CI replays the same faults a day later: new times, new incident id.
    later = incident(T0 + timedelta(days=1))
    # ...under a fresh run id, so node names differ too.
    replay = ReplayProvider(cassette)
    replay.begin("ci123456/f1", later)
    turn = await replay.start("sys", "user").next([])
    args = turn.tool_calls[0].arguments
    assert args is not None and args["start"] == "2026-10-01T01:30:00Z"
    assert args["node_id"] == "ci123456-h100-node-01"
    assert turn.tool_calls[1].arguments == {"incident_id": str(later.incident_id)}
    assert anchor(later) - anchor(recorded_incident) == timedelta(days=1)
    with pytest.raises(ProviderError, match="exhausted"):
        await replay.start("s", "u").next([])
    with pytest.raises(ProviderError, match="no recording"):
        replay.begin("ci123456/unknown", later)


def test_kappa_and_calibration(tmp_path: Path) -> None:
    assert cohen_kappa([(True, True), (False, False)]) == 1.0
    assert cohen_kappa([(True, False), (False, True)]) == -1.0
    report = {
        "cases": [
            {
                "case_id": "a",
                "citation_verdicts": [
                    {"chunk_id": "c1", "supported": True},
                    {"chunk_id": "c2", "supported": True},
                ],
            }
        ]
    }
    grades = tmp_path / "g.jsonl"
    grades.write_text('{"case_id": "a", "chunk_id": "c1", "supported": true}\n'
                      '{"case_id": "a", "chunk_id": "c2", "supported": false}\n'
                      '{"case_id": "b", "chunk_id": "c9", "supported": true}\n')  # fmt: skip
    out = calibrate(report, grades)
    assert out["pairs"] == 2 and out["agreement"] == 0.5
    assert out["confusion"]["judge_lenient"] == 1
    assert out["missing_from_report"] == [["b", "c9"]]


def test_ci_gate() -> None:
    cited = ["rb-003-power-fault#remediation"]
    results = [score_case(case("power_fault"), result("power_fault", "replace_part", cited))]
    expected = _gate_values(summarize(results))
    assert _check_gate(summarize(results), expected) == 0
    broken = [score_case(case("power_fault"), result(None, None, tool_errors=2))]
    assert _check_gate(summarize(broken), expected) == 1
