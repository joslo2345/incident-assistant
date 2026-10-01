"""Investigation tools, defined once and served two ways: in-process to the agent loop, and over
MCP (`agent mcp`) to any MCP client such as Claude Desktop or the MCP Inspector.

Every tool is read-only except `drain_node`, which only files an approval request. Arguments are
validated with Pydantic before a tool runs; a bad call returns an error message the model can act
on, never an exception. Results are JSON, capped in size so one call can't flood the context.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from agent.data import Approvals, Chunk, FleetData, Knowledge
from agent.llm import ToolSpec
from incident_contracts import FailureType

MAX_RESULT_CHARS = 12_000
MAX_CHUNK_CHARS = 1_500
MAX_METRICS_WINDOW = timedelta(hours=6)
MAX_LOGS_WINDOW = timedelta(hours=24)
MAX_SERIES_POINTS = 48

NodeId = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9._-]*$")]


class Args(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def parse_time(value: str) -> datetime:
    """ISO 8601; a time without a timezone is taken as UTC (models often drop the Z)."""
    t = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=UTC)


class Window(Args):
    start: str = Field(description="Window start, ISO 8601 UTC, e.g. 2026-09-29T10:40:00Z")
    end: str = Field(description="Window end, ISO 8601 UTC")

    @field_validator("start", "end")
    @classmethod
    def _iso(cls, v: str) -> str:
        parse_time(v)
        return v

    def bounds(self, limit: timedelta) -> tuple[datetime, datetime]:
        start, end = parse_time(self.start), parse_time(self.end)
        if end <= start:
            raise ToolError("end must be after start")
        if end - start > limit:
            raise ToolError(f"window too long: at most {limit} per call; narrow it")
        return start, end


class GetIncident(Args):
    incident_id: uuid.UUID


class GetMetrics(Window):
    node_id: NodeId
    gpu_index: int | None = Field(
        default=None,
        ge=0,
        lt=16,
        description="One GPU for a time series; omit for a per-GPU summary of the whole node",
    )


class GetLogs(Window):
    node_id: NodeId
    contains: str | None = Field(
        default=None, max_length=100, description='Case-insensitive text filter, e.g. "PSU"'
    )
    limit: int = Field(default=40, ge=1, le=100)


class SearchRunbooks(Args):
    query: str = Field(min_length=3, max_length=500)
    failure_type: FailureType | None = Field(
        default=None, description="Restrict to documents about this failure type"
    )
    k: int = Field(default=4, ge=1, le=8)


class FindSimilar(Args):
    incident_id: uuid.UUID
    k: int = Field(default=3, ge=1, le=5)


class GetInventory(Args):
    node_id: NodeId


class DrainNode(Args):
    node_id: NodeId
    reason: str = Field(min_length=10, max_length=2000)


class ToolError(Exception):
    """A problem the model can fix by calling differently; its message goes back to the model."""


@dataclass
class ToolContext:
    """Per-run state: who is asking, and which knowledge chunks the model has been shown (only
    those may be cited)."""

    requested_by: str = "mcp-client"
    incident_id: uuid.UUID | None = None
    run_id: uuid.UUID | None = None
    seen_chunks: dict[str, Chunk] = field(default_factory=dict)


@dataclass
class Toolbox:
    data: FleetData
    knowledge: Knowledge
    approvals: Approvals

    async def get_incident(self, a: GetIncident, ctx: ToolContext) -> dict[str, Any]:
        incident = await self.data.incident(a.incident_id)
        if incident is None:
            raise ToolError(f"no incident {a.incident_id}")
        record = incident.model_dump(mode="json", exclude={"diagnosis"})
        return record

    async def get_metrics(self, a: GetMetrics, ctx: ToolContext) -> dict[str, Any]:
        start, end = a.bounds(MAX_METRICS_WINDOW)
        minutes = max(1, int((end - start).total_seconds() // 60))
        step = timedelta(minutes=max(1, -(-minutes // MAX_SERIES_POINTS)))
        rows = await self.data.metrics(a.node_id, a.gpu_index, start, end, step)
        if a.gpu_index is None:
            return {
                "node_id": a.node_id,
                "window": [a.start, a.end],
                "per_gpu_summary": rows,
                "note": "samples: about 6 per GPU per minute is normal; fewer means missing data",
            }
        return {
            "node_id": a.node_id,
            "gpu_index": a.gpu_index,
            "window": [a.start, a.end],
            "step_minutes": int(step.total_seconds() // 60),
            "series": rows,
            "note": "counters (*_total) are cumulative; samples per step below 6 x step_minutes "
            "means missing data" + ("" if rows else ". No data for this GPU in the window."),
        }

    async def get_logs(self, a: GetLogs, ctx: ToolContext) -> dict[str, Any]:
        start, end = a.bounds(MAX_LOGS_WINDOW)
        rows, total = await self.data.logs(a.node_id, start, end, a.contains, a.limit)
        return {
            "node_id": a.node_id,
            "window": [a.start, a.end],
            "entries": rows,
            "total_matching": total,
            "note": f"showing the first {len(rows)} of {total}; narrow the window to see others"
            if total > len(rows)
            else "all matching entries shown (XID = GPU driver error; bmc = baseboard log)",
        }

    async def search_runbooks(self, a: SearchRunbooks, ctx: ToolContext) -> dict[str, Any]:
        ftype = a.failure_type.value if a.failure_type else None
        hits = await self.knowledge.search(a.query, a.k, ftype, None)
        return {"query": a.query, "results": _cite(hits, ctx)}

    async def find_similar_incidents(self, a: FindSimilar, ctx: ToolContext) -> dict[str, Any]:
        incident = await self.data.incident(a.incident_id)
        if incident is None:
            raise ToolError(f"no incident {a.incident_id}")
        signals = "; ".join(dict.fromkeys(e.description for e in incident.evidence))[:400]
        hits = await self.knowledge.search(f"{incident.title}. {signals}", a.k, None, "incident")
        node_id = incident.components[0].node_id
        history = [
            r
            for r in await self.data.node_incidents(node_id, 6)
            if r["incident_id"] != a.incident_id
        ][:5]
        return {
            "similar_past_incident_reports": _cite(hits, ctx),
            "other_incidents_on_this_node": history,
        }

    async def get_node_inventory(self, a: GetInventory, ctx: ToolContext) -> dict[str, Any]:
        gpus = await self.data.gpus(a.node_id)
        if not gpus:
            raise ToolError(f"no telemetry has ever been seen for node {a.node_id!r}")
        return {
            "node_id": a.node_id,
            "gpus": gpus,
            "recent_incidents": await self.data.node_incidents(a.node_id, 5),
            "approval_requests": await self.approvals.for_node(a.node_id, 5),
            "note": "last_seen is each GPU's latest sample; power_limit_w is the enforced cap",
        }

    async def drain_node(self, a: DrainNode, ctx: ToolContext) -> dict[str, Any]:
        if ctx.incident_id is not None:
            # Inside an investigation, only the incident's own node. Log text the model reads
            # (BMC messages, runbooks) must not be able to steer requests at other nodes.
            incident = await self.data.incident(ctx.incident_id)
            node = incident.components[0].node_id if incident else None
            if a.node_id != node:
                raise ToolError(f"this investigation can only request a drain of {node!r}")
        if not await self.data.gpus(a.node_id):
            raise ToolError(f"unknown node {a.node_id!r}")
        row, created = await self.approvals.request(
            "drain_node", a.node_id, None, a.reason, ctx.requested_by, ctx.incident_id, ctx.run_id
        )
        return {
            "request_id": row["request_id"],
            "status": row["status"],
            "created": created,
            "note": "The node has NOT been drained. A human must approve this request first."
            + ("" if created else " A pending request for this node already existed."),
        }


def _cite(hits: list[Chunk], ctx: ToolContext) -> list[dict[str, Any]]:
    out = []
    for h in hits:
        ctx.seen_chunks[h.chunk_id] = h
        text = h.text if len(h.text) <= MAX_CHUNK_CHARS else h.text[:MAX_CHUNK_CHARS] + " [...]"
        out.append(
            {"chunk_id": h.chunk_id, "kind": h.doc_kind, "title": h.title, "heading": h.heading,
             "text": text}
        )  # fmt: skip
    return out


Handler = Callable[[Toolbox, Any, ToolContext], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args: type[Args]
    handler: Handler
    read_only: bool = True

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(self.name, self.description, json_schema(self.args))


def json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Flat JSON Schema with enums inlined: small local models handle `$ref` poorly."""
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})

    def inline(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return inline(defs[node["$ref"].rsplit("/", 1)[-1]])
            return {k: inline(v) for k, v in node.items() if k != "title"}
        if isinstance(node, list):
            return [inline(v) for v in node]
        return node

    return inline(schema)  # type: ignore[no-any-return]


TOOLS: dict[str, Tool] = {
    t.name: t
    for t in [
        Tool(
            "get_incident",
            "Fetch an incident opened by the detector: severity, suspected failure type, affected "
            "components, and evidence items (each with an evidence_id, signal, value, threshold "
            "and time window).",
            GetIncident,
            Toolbox.get_incident,
        ),
        Tool(
            "get_metrics",
            "GPU telemetry from the per-minute rollup: temperature, power and enforced power "
            "limit, utilization, SM clock, throttling, sample counts, and cumulative ECC, PCIe "
            "replay and NVLink CRC error counters. With gpu_index: a time series for that GPU. "
            "Without: a per-GPU summary of the node, useful to compare a GPU with its neighbours. "
            "Window at most 6 hours.",
            GetMetrics,
            Toolbox.get_metrics,
        ),
        Tool(
            "get_logs",
            "GPU driver XID errors and BMC (baseboard management) log entries for a node, in time "
            "order: fans, PSUs, PCIe, temperatures. Optional text filter. Window at most 24 hours.",
            GetLogs,
            Toolbox.get_logs,
        ),
        Tool(
            "search_runbooks",
            "Search the team's runbooks and past incident reports (hybrid keyword + semantic "
            "search, reranked). Returns passages with chunk_ids; cite these chunk_ids in the "
            "diagnosis.",
            SearchRunbooks,
            Toolbox.search_runbooks,
        ),
        Tool(
            "find_similar_incidents",
            "Past incident reports that resemble this incident, and other recent incidents on "
            "the same node. Returned report passages can be cited by chunk_id.",
            FindSimilar,
            Toolbox.find_similar_incidents,
        ),
        Tool(
            "get_node_inventory",
            "A node's GPUs (model, UUID, memory, enforced power limit, last sample time), its "
            "recent incidents, and its approval requests (e.g. pending drains).",
            GetInventory,
            Toolbox.get_node_inventory,
        ),
        Tool(
            "drain_node",
            "Request that a node be drained (no new jobs scheduled on it). This does NOT drain "
            "anything: it files a request that a human must approve. Use only when the evidence "
            "shows the node should stop taking work.",
            DrainNode,
            Toolbox.drain_node,
            read_only=False,
        ),
    ]
}


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, float):
        return round(value, 3)
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def to_json(result: dict[str, Any]) -> str:
    text = json.dumps(result, default=_jsonable, separators=(",", ":"))
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + '..."[truncated: narrow the query]"'
    return text


@dataclass(frozen=True)
class Outcome:
    content: str
    is_error: bool
    result: dict[str, Any] | None = None


async def call_tool(
    toolbox: Toolbox,
    name: str,
    arguments: dict[str, Any] | None,
    ctx: ToolContext,
    allowed: frozenset[str] | None = None,
) -> Outcome:
    """Validate and run one tool call. Never raises for a bad call: returns an error outcome."""
    tool = TOOLS.get(name)
    if tool is None or (allowed is not None and name not in allowed):
        return Outcome(f"Error: unknown tool {name!r}. Available: {sorted(allowed or TOOLS)}", True)
    if arguments is None:
        return Outcome("Error: arguments were not valid JSON; send a JSON object.", True)
    try:
        args = tool.args.model_validate(arguments)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(map(str, e['loc'])) or 'arguments'}: {e['msg']}" for e in exc.errors()
        )
        return Outcome(f"Error: invalid arguments for {name}: {problems}", True)
    try:
        result = await tool.handler(toolbox, args, ctx)
    except ToolError as exc:
        return Outcome(f"Error: {exc}", True)
    return Outcome(to_json(result), False, result)
