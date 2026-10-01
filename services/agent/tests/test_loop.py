from typing import Any

import pytest
from agent_testkit import (
    NODE,
    PAST,
    RUNBOOK,
    FakeData,
    call,
    make_incident,
    toolbox,
    turn,
    valid_submission,
    window,
)

from agent.diagnosis import SUBMIT
from agent.llm import Price, ProviderError, ScriptedProvider, Turn
from agent.loop import Budget, Investigator, render_incident
from agent.trace import MemoryTraceStore
from incident_contracts import ActionType, ComponentKind, FailureType


def investigator(
    script: list[Any], budget: Budget | None = None, data: FakeData | None = None, **kw: Any
) -> tuple[Investigator, ScriptedProvider, MemoryTraceStore, Any]:
    tb, _, _, approvals = toolbox(data)
    provider = ScriptedProvider(script, price=Price(input=4.0, output=20.0))
    traces = MemoryTraceStore()
    return Investigator(provider, tb, approvals, traces, budget, **kw), provider, traces, approvals


SEARCH = call("search_runbooks", {"query": "fan failure 0 RPM", "failure_type": "thermal_runaway"})


async def test_cited_diagnosis_end_to_end() -> None:
    inv, _, traces, approvals = investigator([
        turn(call("get_metrics", {"node_id": NODE, "gpu_index": 3, **window()}),
             call("get_logs", {"node_id": NODE, **window()})),
        turn(SEARCH),
        turn(call(SUBMIT, valid_submission())),
    ])  # fmt: skip
    result = await inv.investigate(make_incident())

    assert result.run.status == "succeeded", result.run.error
    d = result.diagnosis
    assert d is not None
    assert d.root_cause == FailureType.THERMAL_RUNAWAY
    assert [c.chunk_id for c in d.citations] == [RUNBOOK.chunk_id]
    assert d.citations[0].doc_id == RUNBOOK.doc_id
    assert d.trace_id == result.run.run_id
    assert d.recommended_action.target is not None
    assert d.recommended_action.target.kind == ComponentKind.NODE

    # Tokens and cost add up over the three model calls (1000 in / 100 out each).
    run = traces.runs[result.run.run_id]
    assert (run.steps, run.tool_calls) == (3, 3)
    assert (run.input_tokens, run.output_tokens) == (3000, 300)
    assert run.cost_usd == pytest.approx(3000 * 4 / 1e6 + 300 * 20 / 1e6)
    assert run.latency_ms is not None and run.diagnosis is not None
    kinds = [(s.kind, s.name) for s in traces.steps[run.run_id]]
    assert kinds == [
        ("model", "scripted"), ("tool", "get_metrics"), ("tool", "get_logs"),
        ("model", "scripted"), ("tool", "search_runbooks"),
        ("model", "scripted"), ("tool", SUBMIT),
    ]  # fmt: skip
    assert [s.seq for s in traces.steps[run.run_id]] == list(range(7))

    # The drain recommendation became a pending approval request, not an action.
    assert result.approval_request is not None
    assert result.approval_request["status"] == "pending"
    assert result.approval_request["requested_by"] == f"agent:{run.run_id}"
    assert len(approvals.rows) == 1


async def test_citation_of_unseen_chunk_is_rejected_then_fixed() -> None:
    def retry(session: Any) -> Turn:
        rejection = session.transcript[-1][1][0]
        assert rejection.is_error and "not returned" in rejection.content
        return turn(call(SUBMIT, valid_submission()))

    inv, *_ = investigator([
        turn(SEARCH),
        turn(call(SUBMIT, valid_submission(citations=["runbooks/made-up.md#x"]))),
        retry,
    ])  # fmt: skip
    result = await inv.investigate(make_incident())
    assert result.run.status == "succeeded"


async def test_unknown_evidence_ids_are_rejected() -> None:
    inv, *_ = investigator(
        [turn(SEARCH)] + [turn(call(SUBMIT, valid_submission(evidence_ids=["ev-9"])))] * 3
    )
    result = await inv.investigate(make_incident())
    assert result.run.status == "invalid_output"
    assert result.diagnosis is None
    assert "3 invalid" in (result.run.error or "")


async def test_uncited_diagnosis_is_rejected_unless_unknown() -> None:
    no_cite = valid_submission(citations=[], action="monitor")
    unknown = valid_submission(root_cause="unknown", citations=[], evidence_ids=[],
                               action="escalate")  # fmt: skip
    inv, *_ = investigator([turn(call(SUBMIT, no_cite)), turn(call(SUBMIT, unknown))])
    result = await inv.investigate(make_incident())
    assert result.run.status == "succeeded"
    assert result.diagnosis is not None and result.diagnosis.root_cause == FailureType.UNKNOWN


async def test_step_budget_forces_a_submission_then_stops() -> None:
    inv, provider, *_ = investigator(
        [turn(call("get_logs", {"node_id": NODE, **window()}))] * 10, Budget(max_steps=4)
    )
    result = await inv.investigate(make_incident())
    assert result.run.status == "budget_exceeded"
    assert result.run.steps == 4
    session = provider.sessions[0]
    # The last call offered nothing but submit_diagnosis, after a wrap-up message.
    assert session.offered_tools[-1] == [SUBMIT]
    assert len(session.offered_tools[0]) == 8
    assert any(kind == "user" and "Budget almost used up" in str(m)
               for kind, m in session.transcript)  # fmt: skip
    # Tool calls made after the wrap-up are refused rather than run.
    assert "only submit_diagnosis" in session.transcript[-1][1][0].content


async def test_context_guard_wraps_up_before_the_window_fills() -> None:
    big = turn(call("get_logs", {"node_id": NODE, **window()}), tokens=(27_000, 50))
    unknown = valid_submission(citations=[], root_cause="unknown", evidence_ids=[], action="none")
    inv, provider, *_ = investigator([big, turn(call(SUBMIT, unknown))])
    result = await inv.investigate(make_incident())
    assert result.run.status == "succeeded"
    assert provider.sessions[0].offered_tools[1] == [SUBMIT]


async def test_token_budget() -> None:
    inv, *_ = investigator(
        [turn(call("get_logs", {"node_id": NODE, **window()}), tokens=(6000, 0))] * 5,
        Budget(max_tokens=10_000, max_context_tokens=100_000),
    )
    result = await inv.investigate(make_incident())
    assert result.run.status == "budget_exceeded" and "token" in (result.run.error or "")


async def test_allowlist_and_hallucinated_tools() -> None:
    inv, provider, *_ = investigator(
        [turn(call("drain_node", {"node_id": NODE, "reason": "fan failed, drain it"}),
              call("run_shell", {"cmd": "reboot"})),
         turn(call(SUBMIT, valid_submission(citations=[], root_cause="unknown", evidence_ids=[],
                                            action="monitor")))],
        tools=frozenset({"get_logs", "get_metrics"}),
    )  # fmt: skip
    result = await inv.investigate(make_incident())
    results = provider.sessions[0].transcript[2][1]
    assert all(r.is_error and "unknown tool" in r.content for r in results)
    assert result.run.status == "succeeded"
    assert sorted(provider.sessions[0].offered_tools[0]) == ["get_logs", "get_metrics", SUBMIT]


async def test_noisy_neighbor_monitor_files_no_approval() -> None:
    sub = valid_submission(root_cause="noisy_neighbor", action="monitor", confidence=0.7)
    inv, _, _, approvals = investigator([turn(SEARCH), turn(call(SUBMIT, sub))])
    result = await inv.investigate(make_incident())
    assert result.diagnosis is not None
    assert result.diagnosis.recommended_action.action == ActionType.MONITOR
    assert result.approval_request is None and approvals.rows == []


async def test_gpu_reset_targets_the_gpu() -> None:
    sub = valid_submission(action="reset_gpu", action_gpu_index=3)
    inv, _, _, approvals = investigator([turn(SEARCH), turn(call(SUBMIT, sub))])
    result = await inv.investigate(make_incident())
    assert result.diagnosis is not None
    target = result.diagnosis.recommended_action.target
    assert target is not None and (target.kind, target.gpu_index) == (ComponentKind.GPU, 3)
    assert approvals.rows[0]["action"] == "reset_gpu" and approvals.rows[0]["gpu_index"] == 3


async def test_slow_tool_times_out_without_ending_the_run() -> None:
    inv, provider, *_ = investigator(
        [turn(call("get_metrics", {"node_id": NODE, **window()})), turn(SEARCH),
         turn(call(SUBMIT, valid_submission()))],
        Budget(tool_timeout_s=0.01),
        data=FakeData(incidents={}, delay_s=1.0),
    )  # fmt: skip
    result = await inv.investigate(make_incident())
    assert "timed out" in provider.sessions[0].transcript[2][1][0].content
    assert result.run.status == "succeeded"


async def test_model_outage_is_recorded() -> None:
    def down(_: Any) -> Turn:
        raise ProviderError("connection refused")

    inv, _, traces, _ = investigator([turn(SEARCH), down])
    result = await inv.investigate(make_incident())
    assert result.run.status == "error" and "connection refused" in (result.run.error or "")
    # The steps that did happen are still in the trace.
    assert [s.name for s in traces.steps[result.run.run_id]] == ["scripted", "search_runbooks"]


async def test_model_that_stops_calling_tools_is_nudged_then_failed() -> None:
    inv, *_ = investigator([turn(text="I think it's the fan.")] * 5)
    result = await inv.investigate(make_incident())
    assert result.run.status == "invalid_output"
    assert result.run.steps == 3


async def test_past_incident_citation_is_accepted() -> None:
    sim = call("find_similar_incidents", {"incident_id": str(make_incident().incident_id)})
    inv, *_ = investigator(
        [turn(sim), turn(call(SUBMIT, valid_submission(citations=[PAST.chunk_id])))]
    )
    result = await inv.investigate(make_incident())
    assert result.run.status == "succeeded"


def test_incident_prompt_lists_evidence_and_a_query_window() -> None:
    text = render_incident(make_incident())
    assert "ev-0" in text and "ev-1" in text and "FAN3" in text
    assert "hypothesis" not in text  # framing lives in the system prompt
    assert "Suggested window for queries: 2026-09-29T10:10:00Z to 2026-09-29T11:02:00Z" in text


def test_allowlist_must_name_real_tools() -> None:
    with pytest.raises(ValueError, match="unknown tools"):
        investigator([], tools=frozenset({"get_logs", "format_disk"}))
