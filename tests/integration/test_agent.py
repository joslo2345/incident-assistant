"""Investigation agent against the live stack: database guarantees for approvals, and an injected
fault investigated end to end by the configured model (Ollama on the host by default)."""

import json
import secrets
import subprocess
import urllib.error
import urllib.request
from typing import Any

import pytest

from .conftest import REPO, sql


def env() -> dict[str, str]:
    return dict(
        line.split("=", 1)
        for line in (REPO / "deploy" / ".env").read_text().splitlines()
        if "=" in line and not line.startswith("#")
    )


def api(path: str, body: dict[str, Any] | None = None, key: str = "AGENT_API_KEY") -> Any:
    req = urllib.request.Request(
        f"http://localhost:8002{path}",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"X-API-Key": env()[key], "Content-Type": "application/json"},
        method="POST" if body is not None else "GET",
    )
    with urllib.request.urlopen(req, timeout=900) as resp:
        return json.load(resp)


def as_role(role: str, statement: str) -> subprocess.CompletedProcess[str]:
    """Run a statement as a service role (via SET ROLE, so no password is needed here)."""
    from .conftest import COMPOSE

    script = f"SET ROLE {role}; {statement}"
    return subprocess.run(
        [*COMPOSE, "exec", "-T", "timescaledb", "psql", "-U", "postgres", "-d", "telemetry",
         "-v", "ON_ERROR_STOP=1", "-tAc", script],
        capture_output=True, text=True,
    )  # fmt: skip


def test_approval_requests_are_guarded_by_the_database() -> None:
    node = f"it-approval-{secrets.token_hex(3)}"
    rid = sql(
        "INSERT INTO approval_requests (request_id, action, node_id, reason, requested_by) "
        f"VALUES (gen_random_uuid(), 'drain_node', '{node}', 'test', 'agent:test') "
        "RETURNING request_id"
    ).splitlines()[0]

    def decide(role: str, status: str, by: str) -> subprocess.CompletedProcess[str]:
        return as_role(role, f"UPDATE approval_requests SET status = '{status}', "
                             f"decided_by = '{by}' WHERE request_id = '{rid}'")  # fmt: skip

    # The agent role can file requests but not decide them.
    denied = decide("incident_agent", "approved", "alice")
    assert denied.returncode != 0 and "permission denied" in denied.stderr
    # The approver role can decide but not file requests.
    denied = as_role("approval_service", "INSERT INTO approval_requests (request_id, action, "
                     "node_id, reason, requested_by) VALUES (gen_random_uuid(), 'drain_node', "
                     "'x', 'y', 'z')")  # fmt: skip
    assert denied.returncode != 0 and "permission denied" in denied.stderr
    # Nobody can approve as an agent, or change the request itself while deciding.
    assert "decided by a person" in decide("approval_service", "approved", "agent:x").stderr
    rewrite = as_role(
        "approval_service",
        f"UPDATE approval_requests SET reason = 'other' WHERE request_id = '{rid}'",
    )
    assert "permission denied" in rewrite.stderr  # no UPDATE grant on reason
    ok = decide("approval_service", "approved", "alice")
    assert ok.returncode == 0, ok.stderr
    # A decision is final.
    assert "already approved" in decide("approval_service", "rejected", "bob").stderr
    sql(f"DELETE FROM approval_requests WHERE node_id = '{node}'")


def test_injected_fault_gets_a_cited_diagnosis() -> None:
    try:
        api("/healthz")
    except urllib.error.URLError:
        pytest.fail("agent API not reachable on :8002; run `make up`")
    run_id = f"ia-{secrets.token_hex(3)}"
    subprocess.run(
        ["uv", "run", "python", "scripts/eval_detection.py", "--run-id", run_id,
         "--duration", "3h", "--faults", "1", "--seed", "3", "--bmc-dropout", "0"],
        cwd=REPO, check=True, capture_output=True, text=True,
    )  # fmt: skip
    truth = json.loads((REPO / "eval" / "runs" / run_id / "ground_truth.jsonl").read_text())
    incident_id = sql(
        f"SELECT incident_id FROM incidents WHERE detector_run = '{run_id}-v3-residual' "
        f"AND node_id = '{truth['node_id']}' AND severity <> 'sev4' ORDER BY opened_at LIMIT 1"
    )
    assert incident_id, "the detector did not open an incident for the injected fault"

    result = api(f"/v1/incidents/{incident_id}/investigate", {})
    print(json.dumps({k: result[k] for k in ("status", "steps", "tool_calls", "input_tokens",
                                             "output_tokens", "latency_ms")}))  # fmt: skip
    print(json.dumps(result["diagnosis"], indent=2))
    assert result["status"] == "succeeded", result["error"]
    diagnosis = result["diagnosis"]
    assert diagnosis["root_cause"] == truth["type"]
    # Every citation is a real chunk in the knowledge base.
    cited = [c["chunk_id"] for c in diagnosis["citations"]]
    assert cited
    found = sql(
        "SELECT count(*) FROM kb_chunks WHERE chunk_id IN ("
        + ",".join("'" + c.replace("'", "''") + "'" for c in cited)
        + ")"
    )
    assert int(found) == len(cited)
    # The run is traced: every model and tool call, with latency.
    run = api(f"/v1/runs/{result['run_id']}")
    assert run["status"] == "succeeded" and run["latency_ms"] > 0
    assert {s["kind"] for s in run["trace"]} == {"model", "tool"}
    assert sum(s["kind"] == "model" for s in run["trace"]) == result["steps"]
    # A state-changing recommendation exists only as a pending request.
    if diagnosis["recommended_action"]["action"] in {"drain_node", "reset_gpu"}:
        assert result["approval_request"]["status"] == "pending"
