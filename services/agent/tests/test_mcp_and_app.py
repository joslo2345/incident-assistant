import json
import uuid

from agent_testkit import INCIDENT_ID, NODE, RUNBOOK, call, toolbox, turn, valid_submission
from mcp import Client
from web_testkit import app_client

from agent.diagnosis import SUBMIT
from agent.mcp_server import build_server


async def test_mcp_lists_tools_with_hints_and_serves_calls() -> None:
    tb, _, _, approvals = toolbox()
    async with Client(build_server(tb)) as client:
        listed = await client.list_tools()
        tools = {t.name: t for t in listed.tools}
        assert set(tools) == {
            "get_incident", "get_metrics", "get_logs", "search_runbooks",
            "find_similar_incidents", "get_node_inventory", "drain_node",
        }  # fmt: skip
        assert SUBMIT not in tools  # internal to the agent loop
        assert tools["get_logs"].annotations is not None
        assert tools["get_logs"].annotations.read_only_hint is True
        assert tools["drain_node"].annotations is not None
        assert tools["drain_node"].annotations.destructive_hint is True

        result = await client.call_tool("search_runbooks", {"query": "fan at 0 RPM"})
        assert not result.is_error
        assert RUNBOOK.chunk_id in json.loads(result.content[0].text)["results"][0]["chunk_id"]  # type: ignore[union-attr]

        bad = await client.call_tool("get_metrics", {"node_id": NODE})
        assert bad.is_error and "start" in bad.content[0].text  # type: ignore[union-attr]

        reason = "fan failure, needs a repair"
        drained = await client.call_tool("drain_node", {"node_id": NODE, "reason": reason})
        assert not drained.is_error
    assert approvals.rows[0]["requested_by"] == "mcp-client"
    assert approvals.rows[0]["status"] == "pending"


AGENT = {"X-API-Key": "agent-key"}


def test_investigate_endpoint() -> None:
    search = call("search_runbooks", {"query": "fan failure"})
    c, *_ = app_client([turn(search), turn(call(SUBMIT, valid_submission()))])
    with c:
        assert c.post(f"/v1/incidents/{INCIDENT_ID}/investigate").status_code == 401
        missing = c.post(f"/v1/incidents/{uuid.uuid4()}/investigate", headers=AGENT)
        assert missing.status_code == 404
        r = c.post(f"/v1/incidents/{INCIDENT_ID}/investigate", headers=AGENT)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "succeeded"
        assert body["diagnosis"]["root_cause"] == "thermal_runaway"
        assert body["approval_request"]["status"] == "pending"
        pending = c.get("/v1/approvals", params={"status": "pending"}, headers=AGENT).json()
        assert len(pending) == 1
        metrics = c.get("/metrics").text
        assert 'agent_runs_total{provider="scripted",status="succeeded"} 1.0' in metrics


def test_the_typed_name_approval_endpoint_is_gone() -> None:
    c, *_ = app_client([])
    with c:
        r = c.post(f"/v1/approvals/{uuid.uuid4()}/decision",
                   json={"decision": "approve", "decided_by": "alice"}, headers=AGENT)  # fmt: skip
        assert r.status_code in {404, 405}
