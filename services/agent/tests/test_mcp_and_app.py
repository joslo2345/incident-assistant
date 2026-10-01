import json
import uuid
from dataclasses import dataclass
from typing import Any

from agent_testkit import INCIDENT_ID, NODE, RUNBOOK, call, toolbox, turn, valid_submission
from fastapi.testclient import TestClient
from mcp import Client

from agent.app import Services, create_app
from agent.data import DecisionError
from agent.diagnosis import SUBMIT
from agent.llm import ScriptedProvider
from agent.loop import Investigator
from agent.mcp_server import build_server
from agent.runtime import Settings
from agent.trace import MemoryTraceStore


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


@dataclass
class FakeReader:
    async def run(self, run_id: uuid.UUID) -> dict[str, Any] | None:
        return None

    async def latest_diagnosis(self, incident_id: uuid.UUID) -> dict[str, Any] | None:
        return None

    async def next_uninvestigated(self, detector_run: str) -> uuid.UUID | None:
        return None


@dataclass
class FakeDecider:
    decided: list[tuple[uuid.UUID, bool, str]]

    async def decide(
        self, request_id: uuid.UUID, approve: bool, decided_by: str, note: str | None
    ) -> dict[str, Any]:
        if decided_by.startswith("agent"):
            raise DecisionError("an approval must be decided by a person other than the requester")
        self.decided.append((request_id, approve, decided_by))
        return {"request_id": str(request_id), "status": "approved" if approve else "rejected"}


AGENT, APPROVER = {"X-API-Key": "agent-key"}, {"X-API-Key": "approver-key"}


def client(script: list[Any]) -> tuple[TestClient, FakeDecider]:
    tb, data, _, approvals = toolbox()
    decider = FakeDecider([])
    provider = ScriptedProvider(script)
    services = Services(
        data, approvals, FakeReader(),
        lambda: Investigator(provider, tb, approvals, MemoryTraceStore()), decider,
    )  # fmt: skip
    settings = Settings(
        api_keys=frozenset({"agent-key"}), approver_keys=frozenset({"approver-key"})
    )
    return TestClient(create_app(settings, services)), decider


def test_investigate_endpoint() -> None:
    search = call("search_runbooks", {"query": "fan failure"})
    c, _ = client([turn(search), turn(call(SUBMIT, valid_submission()))])
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


def test_only_approvers_can_decide() -> None:
    c, decider = client([])
    rid = uuid.uuid4()
    body = {"decision": "approve", "decided_by": "alice"}
    with c:
        # An agent key is not an approver key.
        assert c.post(f"/v1/approvals/{rid}/decision", json=body, headers=AGENT).status_code == 401
        r = c.post(f"/v1/approvals/{rid}/decision", json=body, headers=APPROVER)
        assert r.status_code == 200 and r.json()["status"] == "approved"
        conflict = c.post(f"/v1/approvals/{rid}/decision",
                          json={**body, "decided_by": "agent-x"}, headers=APPROVER)  # fmt: skip
        assert conflict.status_code == 409
    assert decider.decided == [(rid, True, "alice")]
