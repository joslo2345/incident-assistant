"""In-memory fakes for the agent's dependencies, and a sample incident."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from agent.data import ApprovalAction, Chunk
from agent.llm import ToolCall, Turn, Usage
from agent.tools import Toolbox
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

NODE = "h100-node-02"
T0 = datetime(2026, 9, 29, 10, 40, tzinfo=UTC)
INCIDENT_ID = uuid.UUID("11111111-2222-3333-4444-555555555555")


def make_incident(node: str = NODE, gpu: int = 3) -> Incident:
    gpu_ref = ComponentRef(kind=ComponentKind.GPU, node_id=node, gpu_index=gpu)
    return Incident(
        incident_id=INCIDENT_ID,
        created_at=T0,
        updated_at=T0 + timedelta(minutes=8),
        status=IncidentStatus.OPEN,
        severity=Severity.SEV2,
        title=f"Thermal runaway on {node} GPU {gpu}",
        suspected_failure_type=FailureType.THERMAL_RUNAWAY,
        components=[ComponentRef(kind=ComponentKind.NODE, node_id=node), gpu_ref],
        evidence=[
            Evidence(
                evidence_id="ev-0", detector=DetectorKind.EVENT_MATCH,
                component=ComponentRef(kind=ComponentKind.NODE, node_id=node),
                signal="bmc_fan", window_start=T0, window_end=T0 + timedelta(minutes=1),
                observed_value=0, description="[fan_failure] FAN3 reading 0 RPM",
            ),
            Evidence(
                evidence_id="ev-1", detector=DetectorKind.STATIC_THRESHOLD, component=gpu_ref,
                signal="temperature_c", window_start=T0 + timedelta(minutes=6),
                window_end=T0 + timedelta(minutes=7), observed_value=88.5, threshold=85,
                description="[thermal_throttle] GPU 3 at 88.5 C, clocks throttled",
            ),
        ],
    )  # fmt: skip


RUNBOOK = Chunk(
    "runbooks/thermal.md#fan-failure", "runbooks/thermal.md", "runbook", "Thermal runaway",
    "Fan failure", "If a fan reads 0 RPM, drain the node and replace the fan.", 2.1,
)  # fmt: skip
PAST = Chunk(
    "incidents/INC-0042.md", "incidents/INC-0042.md", "incident", "INC-0042 fan failure",
    "Report", "FAN3 failed on h100-node-05; GPUs 0-3 throttled; fan replaced.", 1.4,
)  # fmt: skip


@dataclass
class FakeData:
    incidents: dict[uuid.UUID, Incident] = field(default_factory=dict)
    nodes: set[str] = field(default_factory=lambda: {NODE})
    metrics_calls: list[tuple[Any, ...]] = field(default_factory=list)
    delay_s: float = 0.0

    async def incident(self, incident_id: uuid.UUID) -> Incident | None:
        return self.incidents.get(incident_id)

    async def metrics(
        self, node_id: str, gpu_index: int | None, start: datetime, end: datetime, step: timedelta
    ) -> list[dict[str, Any]]:
        if self.delay_s:
            import asyncio

            await asyncio.sleep(self.delay_s)
        self.metrics_calls.append((node_id, gpu_index, start, end, step))
        if gpu_index is None:
            return [{"gpu_index": g, "temp_max_c": 70.0 + g, "samples": 60} for g in range(8)]
        return [{"t": start, "temp_max_c": 88.5, "throttled_samples": 6, "samples": 6}]

    async def logs(
        self, node_id: str, start: datetime, end: datetime, contains: str | None, limit: int
    ) -> tuple[list[dict[str, Any]], int]:
        rows = [{"time": T0, "source": "bmc", "gpu_index": None, "severity": "critical",
                 "text": "Fan FAN3 reading 0 RPM [sensor FAN3]"}]  # fmt: skip
        return rows[:limit], len(rows)

    async def gpus(self, node_id: str) -> list[dict[str, Any]]:
        if node_id not in self.nodes:
            return []
        return [{"gpu_index": g, "gpu_model": "NVIDIA H100 80GB HBM3", "last_seen": T0}
                for g in range(8)]  # fmt: skip

    async def node_incidents(self, node_id: str, limit: int) -> list[dict[str, Any]]:
        return [{"incident_id": INCIDENT_ID, "status": "open", "title": "Thermal runaway"}][:limit]


@dataclass
class FakeKnowledge:
    hits: list[Chunk] = field(default_factory=lambda: [RUNBOOK, PAST])
    queries: list[tuple[str, int, str | None, str | None]] = field(default_factory=list)

    async def search(
        self, query: str, k: int, failure_type: str | None, doc_kind: str | None
    ) -> list[Chunk]:
        self.queries.append((query, k, failure_type, doc_kind))
        return [h for h in self.hits if doc_kind is None or h.doc_kind == doc_kind][:k]


@dataclass
class FakeApprovals:
    rows: list[dict[str, Any]] = field(default_factory=list)

    async def request(
        self,
        action: ApprovalAction,
        node_id: str,
        gpu_index: int | None,
        reason: str,
        requested_by: str,
        incident_id: uuid.UUID | None = None,
        run_id: uuid.UUID | None = None,
    ) -> tuple[dict[str, Any], bool]:
        for r in self.rows:
            if (r["action"], r["node_id"], r["gpu_index"], r["status"]) == (
                action, node_id, gpu_index, "pending"
            ):  # fmt: skip
                return r, False
        row = {
            "request_id": uuid.uuid4(), "action": action, "node_id": node_id,
            "gpu_index": gpu_index, "reason": reason, "requested_by": requested_by,
            "incident_id": incident_id, "run_id": run_id, "status": "pending",
        }  # fmt: skip
        self.rows.append(row)
        return row, True

    async def for_node(self, node_id: str, limit: int) -> list[dict[str, Any]]:
        return [r for r in self.rows if r["node_id"] == node_id][:limit]

    async def list(self, status: str | None, limit: int) -> list[dict[str, Any]]:
        return [r for r in self.rows if status is None or r["status"] == status][:limit]


def toolbox(data: FakeData | None = None) -> tuple[Toolbox, FakeData, FakeKnowledge, FakeApprovals]:
    data = data or FakeData(incidents={INCIDENT_ID: make_incident()})
    knowledge, approvals = FakeKnowledge(), FakeApprovals()
    return Toolbox(data, knowledge, approvals), data, knowledge, approvals


_ids = iter(range(1_000_000))


def call(name: str, args: dict[str, Any] | None, raw: str = "") -> ToolCall:
    return ToolCall(f"call_{next(_ids)}", name, args, raw)


def turn(*calls: ToolCall, text: str = "", tokens: tuple[int, int] = (1000, 100)) -> Turn:
    stop = "tool_use" if calls else "end_turn"
    return Turn(text, list(calls), Usage(*tokens), stop, "scripted")  # type: ignore[arg-type]


def valid_submission(**overrides: Any) -> dict[str, Any]:
    sub: dict[str, Any] = {
        "root_cause": "thermal_runaway",
        "confidence": 0.85,
        "summary": "FAN3 failed (0 RPM in the BMC log); GPU 3 then throttled at 88.5 C.",
        "evidence_ids": ["ev-0", "ev-1"],
        "citations": [RUNBOOK.chunk_id],
        "action": "drain_node",
        "action_rationale": "The fan has failed; drain the node so the fan can be replaced.",
    }
    return {**sub, **overrides}


def window(minutes: int = 30) -> dict[str, str]:
    return {
        "start": (T0 - timedelta(minutes=minutes)).isoformat(),
        "end": (T0 + timedelta(minutes=15)).isoformat(),
    }


Script = list[Turn | Callable[[Any], Turn]]
