"""Run traces: every model call and tool call, with inputs, outputs, tokens, cost and latency.
Stored in Postgres (agent_runs / agent_steps) for the A6 evaluation; an in-memory store for tests.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

import asyncpg

MAX_TRACE_CHARS = 20_000


@dataclass(frozen=True)
class Step:
    seq: int
    kind: str  # "model" | "tool"
    name: str
    started_at: datetime
    latency_ms: int
    input: Any = None
    output: Any = None
    is_error: bool = False
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class RunSummary:
    run_id: uuid.UUID
    incident_id: uuid.UUID
    provider: str
    model: str
    started_at: datetime
    status: str = "running"
    steps: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int | None = None
    diagnosis: dict[str, Any] | None = None
    error: str | None = None
    config: dict[str, Any] = field(default_factory=dict)


class TraceStore(Protocol):
    async def start(self, run: RunSummary) -> None: ...
    async def step(self, run_id: uuid.UUID, step: Step) -> None: ...
    async def finish(self, run: RunSummary, finished_at: datetime) -> None: ...


def _clip(value: Any) -> str | None:
    """JSON for a jsonb column, clipped so a huge tool result can't bloat the trace table."""
    if value is None:
        return None
    text = json.dumps(value, default=str)
    if len(text) > MAX_TRACE_CHARS:
        text = json.dumps({"truncated": True, "head": text[:MAX_TRACE_CHARS]})
    return text


class PgTraceStore:
    def __init__(self, pool: asyncpg.Pool[Any]) -> None:
        self.pool = pool

    async def start(self, run: RunSummary) -> None:
        await self.pool.execute(
            "INSERT INTO agent_runs (run_id, incident_id, started_at, provider, model, config) "
            "VALUES ($1, $2, $3, $4, $5, $6::jsonb)",
            run.run_id, run.incident_id, run.started_at, run.provider, run.model,
            json.dumps(run.config),
        )  # fmt: skip

    async def step(self, run_id: uuid.UUID, s: Step) -> None:
        await self.pool.execute(
            "INSERT INTO agent_steps (run_id, seq, kind, name, started_at, latency_ms, input, "
            "output, is_error, input_tokens, output_tokens) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8::jsonb, $9, $10, $11)",
            run_id, s.seq, s.kind, s.name, s.started_at, s.latency_ms, _clip(s.input),
            _clip(s.output), s.is_error, s.input_tokens, s.output_tokens,
        )  # fmt: skip

    async def finish(self, run: RunSummary, finished_at: datetime) -> None:
        await self.pool.execute(
            "UPDATE agent_runs SET finished_at = $2, status = $3, steps = $4, tool_calls = $5, "
            "input_tokens = $6, output_tokens = $7, cost_usd = $8, latency_ms = $9, "
            "diagnosis = $10::jsonb, error = $11 WHERE run_id = $1",
            run.run_id, finished_at, run.status, run.steps, run.tool_calls, run.input_tokens,
            run.output_tokens, round(run.cost_usd, 6), run.latency_ms,
            json.dumps(run.diagnosis) if run.diagnosis else None, run.error,
        )  # fmt: skip


@dataclass
class MemoryTraceStore:
    runs: dict[uuid.UUID, RunSummary] = field(default_factory=dict)
    steps: dict[uuid.UUID, list[Step]] = field(default_factory=dict)

    async def start(self, run: RunSummary) -> None:
        self.runs[run.run_id] = run
        self.steps[run.run_id] = []

    async def step(self, run_id: uuid.UUID, step: Step) -> None:
        self.steps[run_id].append(step)

    async def finish(self, run: RunSummary, finished_at: datetime) -> None:
        self.runs[run.run_id] = run


_RUN_COLUMNS = (
    "run_id, incident_id, started_at, finished_at, status, provider, model, steps, tool_calls, "
    "input_tokens, output_tokens, cost_usd::float8 AS cost_usd, latency_ms, diagnosis::text, "
    "error, config::text"
)


def _run_dict(r: asyncpg.Record) -> dict[str, Any]:
    d = dict(r)
    d["diagnosis"] = json.loads(d["diagnosis"]) if d["diagnosis"] else None
    d["config"] = json.loads(d["config"])
    return d


class PgTraceReader:
    def __init__(self, pool: asyncpg.Pool[Any]) -> None:
        self.pool = pool

    async def run(self, run_id: uuid.UUID) -> dict[str, Any] | None:
        r = await self.pool.fetchrow(f"SELECT {_RUN_COLUMNS} FROM agent_runs WHERE run_id = $1",
                                     run_id)  # fmt: skip
        if r is None:
            return None
        steps = await self.pool.fetch(
            "SELECT seq, kind, name, started_at, latency_ms, input::text, output::text, is_error, "
            "input_tokens, output_tokens FROM agent_steps WHERE run_id = $1 ORDER BY seq",
            run_id,
        )
        out = _run_dict(r)
        out["trace"] = [
            {**dict(s), "input": json.loads(s["input"]) if s["input"] else None,
             "output": json.loads(s["output"]) if s["output"] else None}
            for s in steps
        ]  # fmt: skip
        return out

    async def latest_diagnosis(self, incident_id: uuid.UUID) -> dict[str, Any] | None:
        r = await self.pool.fetchrow(
            f"SELECT {_RUN_COLUMNS} FROM agent_runs WHERE incident_id = $1 "
            f"AND status = 'succeeded' AND config->>'kind' IS DISTINCT FROM 'followup' "
            f"ORDER BY started_at DESC LIMIT 1",
            incident_id,
        )
        return _run_dict(r) if r else None

    async def next_uninvestigated(self, detector_run: str) -> uuid.UUID | None:
        """Oldest actionable open incident with no agent run yet (sev4 is informational)."""
        incident_id: uuid.UUID | None = await self.pool.fetchval(
            "SELECT i.incident_id FROM incidents i WHERE i.detector_run = $1 "
            "AND i.status = 'open' AND i.severity <> 'sev4' "
            "AND NOT EXISTS (SELECT 1 FROM agent_runs r WHERE r.incident_id = i.incident_id) "
            "ORDER BY i.opened_at LIMIT 1",
            detector_run,
        )
        return incident_id
