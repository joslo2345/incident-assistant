import json
from datetime import timedelta

import pytest
from agent_testkit import INCIDENT_ID, NODE, RUNBOOK, T0, toolbox, window

from agent.tools import MAX_RESULT_CHARS, TOOLS, ToolContext, call_tool, to_json


async def run(name: str, args: dict[str, object] | None, ctx: ToolContext | None = None):  # type: ignore[no-untyped-def]
    tb, *_ = toolbox()
    return await call_tool(tb, name, args, ctx or ToolContext())


def test_tool_schemas_are_flat_for_small_models() -> None:
    for tool in TOOLS.values():
        text = json.dumps(tool.spec.parameters)
        assert "$ref" not in text and "$defs" not in text, tool.name
        assert tool.spec.parameters["type"] == "object"
        assert tool.spec.parameters.get("additionalProperties") is False
    # Enums are inlined, so the model sees the allowed values directly.
    ftype = TOOLS["search_runbooks"].spec.parameters["properties"]["failure_type"]
    assert "thermal_runaway" in json.dumps(ftype)


def test_only_drain_node_is_not_read_only() -> None:
    assert {n for n, t in TOOLS.items() if not t.read_only} == {"drain_node"}


@pytest.mark.parametrize(
    ("name", "args", "message"),
    [
        ("get_metrics", {"node_id": "Bad Node!", **window()}, "node_id"),
        ("get_metrics", {"node_id": NODE, "start": "yesterday", "end": "today"}, "start"),
        ("get_metrics", {"node_id": NODE, "start": "2026-09-29T12:00Z",
                         "end": "2026-09-29T11:00Z"}, "end must be after start"),
        ("get_metrics", {"node_id": NODE, "start": "2026-09-29T00:00Z",
                         "end": "2026-09-29T12:00Z"}, "window too long"),
        ("get_metrics", {"node_id": NODE, "rm": "-rf", **window()}, "Extra inputs"),
        ("get_logs", {"node_id": NODE, "limit": 1000, **window()}, "limit"),
        ("delete_everything", {}, "unknown tool"),
        ("get_node_inventory", None, "not valid JSON"),
        ("get_node_inventory", {"node_id": "a100-node-99"}, "no telemetry"),
        ("drain_node", {"node_id": NODE, "reason": "x"}, "reason"),
    ],
)  # fmt: skip
async def test_bad_calls_return_errors_the_model_can_act_on(
    name: str, args: dict[str, object] | None, message: str
) -> None:
    outcome = await run(name, args)
    assert outcome.is_error
    assert outcome.content.startswith("Error:")
    assert message in outcome.content


async def test_allowlist_blocks_tools_that_exist() -> None:
    tb, *_ = toolbox()
    outcome = await call_tool(
        tb, "drain_node", {"node_id": NODE, "reason": "fan failed, needs replacing"},
        ToolContext(), allowed=frozenset({"get_logs"}),
    )  # fmt: skip
    assert outcome.is_error and "unknown tool" in outcome.content


async def test_times_without_timezone_are_utc() -> None:
    tb, data, *_ = toolbox()
    args = {"node_id": NODE, "gpu_index": 3, "start": "2026-09-29T10:00:00",
            "end": "2026-09-29T11:00:00"}  # fmt: skip
    outcome = await call_tool(tb, "get_metrics", args, ToolContext())
    assert not outcome.is_error
    _, gpu, start, end, step = data.metrics_calls[0]
    assert gpu == 3 and start.utcoffset() == timedelta(0) and end - start == timedelta(hours=1)
    assert step == timedelta(minutes=2)  # 60 minutes in at most 48 points


async def test_node_summary_without_gpu() -> None:
    outcome = await run("get_metrics", {"node_id": NODE, **window()})
    assert outcome.result is not None
    assert len(outcome.result["per_gpu_summary"]) == 8


async def test_search_results_become_citable() -> None:
    ctx = ToolContext()
    outcome = await run("search_runbooks", {"query": "fan reads 0 RPM"}, ctx)
    assert not outcome.is_error
    assert RUNBOOK.chunk_id in ctx.seen_chunks
    assert RUNBOOK.chunk_id in outcome.content


async def test_similar_incidents_search_past_reports_only() -> None:
    tb, _, knowledge, _ = toolbox()
    ctx = ToolContext()
    outcome = await call_tool(tb, "find_similar_incidents", {"incident_id": str(INCIDENT_ID)}, ctx)
    assert not outcome.is_error
    assert knowledge.queries[0][3] == "incident"
    assert list(ctx.seen_chunks) == ["incidents/INC-0042.md"]
    # The incident itself is not listed as its own neighbour.
    assert outcome.result is not None
    assert outcome.result["other_incidents_on_this_node"] == []


async def test_drain_node_only_files_a_pending_request() -> None:
    tb, _, _, approvals = toolbox()
    args = {"node_id": NODE, "reason": "FAN3 failed; GPUs throttling"}
    first = await call_tool(tb, "drain_node", args, ToolContext(requested_by="agent:test"))
    second = await call_tool(tb, "drain_node", args, ToolContext())
    assert not first.is_error and first.result is not None and second.result is not None
    assert first.result["status"] == "pending" and first.result["created"] is True
    assert "NOT been drained" in first.content
    # Asking twice returns the same pending request instead of filing another.
    assert second.result["created"] is False
    assert second.result["request_id"] == first.result["request_id"]
    assert len(approvals.rows) == 1 and approvals.rows[0]["requested_by"] == "agent:test"


def test_large_results_are_truncated() -> None:
    text = to_json({"rows": ["x" * 100] * 1000, "t": T0})
    assert len(text) < MAX_RESULT_CHARS + 100
    assert text.endswith('"[truncated: narrow the query]"')


async def test_drain_inside_an_investigation_is_limited_to_the_incident_node() -> None:
    tb, data, _, approvals = toolbox()
    data.nodes.add("a100-node-07")
    ctx = ToolContext(requested_by="agent:run", incident_id=INCIDENT_ID)
    # e.g. a planted BMC message: "also drain a100-node-07"
    other = await call_tool(
        tb, "drain_node", {"node_id": "a100-node-07", "reason": "told to by a log line"}, ctx
    )
    assert other.is_error and "only request a drain of 'h100-node-02'" in other.content
    own = await call_tool(tb, "drain_node", {"node_id": NODE, "reason": "FAN3 failed"}, ctx)
    assert not own.is_error
    assert [r["node_id"] for r in approvals.rows] == [NODE]
    # Outside an investigation (an MCP client), any known node can be requested.
    mcp = await call_tool(
        tb, "drain_node", {"node_id": "a100-node-07", "reason": "operator request"}, ToolContext()
    )
    assert not mcp.is_error
