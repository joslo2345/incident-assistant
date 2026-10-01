"""Agent API: investigate an incident, read run traces and diagnoses, and decide approval
requests. With AGENT_WATCH=true it also investigates new live incidents on its own.

Two kinds of credentials: AGENT_API_KEYS for clients of the agent, APPROVER_API_KEYS for people
deciding approval requests. An agent key can't decide, and the decision itself runs as a
database role that can't file requests (migration 007).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Annotated, Any, Literal, Protocol

from fastapi import Depends, FastAPI, HTTPException, Query, Response, status
from fastapi.security import APIKeyHeader
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from pydantic import BaseModel, Field, field_validator
from starlette.requests import Request

from agent.data import Approvals, DecisionError, FleetData
from agent.loop import Investigator, RunResult
from agent.runtime import Settings
from incident_contracts import SCHEMA_VERSION, Diagnosis
from incident_contracts.api import HealthResponse

log = logging.getLogger("agent.app")

API_KEY_HEADER = "X-API-Key"
_key_header = APIKeyHeader(name=API_KEY_HEADER, auto_error=False)

RUNS = Counter("agent_runs_total", "Investigations", ["status", "provider"])
RUN_SECONDS = Histogram(
    "agent_run_seconds", "Investigation latency", buckets=(5, 15, 30, 60, 120, 240, 480, 960)
)
TOKENS = Counter("agent_tokens_total", "Model tokens", ["direction"])
COST = Counter("agent_cost_usd_total", "Model cost in USD")
APPROVALS = Counter("agent_approval_decisions_total", "Approval decisions", ["decision"])


class TraceReader(Protocol):
    async def run(self, run_id: uuid.UUID) -> dict[str, Any] | None: ...
    async def latest_diagnosis(self, incident_id: uuid.UUID) -> dict[str, Any] | None: ...
    async def next_uninvestigated(self, detector_run: str) -> uuid.UUID | None: ...


class Decider(Protocol):
    async def decide(
        self, request_id: uuid.UUID, approve: bool, decided_by: str, note: str | None
    ) -> dict[str, Any]: ...


@dataclass
class Services:
    data: FleetData
    approvals: Approvals
    reader: TraceReader
    investigator: Callable[[], Investigator]
    decider: Decider | None


class InvestigateResponse(BaseModel):
    run_id: uuid.UUID
    incident_id: uuid.UUID
    status: str
    diagnosis: Diagnosis | None
    approval_request: dict[str, Any] | None
    provider: str
    model: str
    steps: int
    tool_calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int | None
    error: str | None


class DecisionRequest(BaseModel):
    decision: Literal["approve", "reject"]
    decided_by: str = Field(min_length=2, max_length=100, pattern=r"^[A-Za-z0-9._@ -]+$")
    note: str | None = Field(default=None, max_length=2000)

    @field_validator("decided_by")
    @classmethod
    def _a_person(cls, v: str) -> str:
        # The database trigger enforces this too; reject early with a clear message.
        if v.strip().lower().startswith("agent"):
            raise ValueError("decisions are made by people, not agents")
        return v.strip()


def _auth(keys: Callable[[Settings], frozenset[str]]) -> Callable[..., str]:
    def check(request: Request, key: Annotated[str | None, Depends(_key_header)]) -> str:
        allowed = keys(request.app.state.settings)
        if key is None or not any(secrets.compare_digest(key, k) for k in allowed):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing or invalid API key")
        return key

    return check


AgentAuth = Annotated[str, Depends(_auth(lambda s: s.api_keys))]
ApproverAuth = Annotated[str, Depends(_auth(lambda s: s.approver_keys))]


def to_response(r: RunResult) -> InvestigateResponse:
    run = r.run
    return InvestigateResponse(
        run_id=run.run_id, incident_id=run.incident_id, status=run.status,
        diagnosis=r.diagnosis, approval_request=r.approval_request, provider=run.provider,
        model=run.model, steps=run.steps, tool_calls=run.tool_calls,
        input_tokens=run.input_tokens, output_tokens=run.output_tokens,
        cost_usd=round(run.cost_usd, 6), latency_ms=run.latency_ms, error=run.error,
    )  # fmt: skip


def create_app(settings: Settings | None = None, services: Services | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    state: dict[str, Any] = {"services": services}
    # One investigation at a time: a local model serves one request at a time anyway.
    lock = asyncio.Lock()

    async def investigate(incident_id: uuid.UUID) -> RunResult | None:
        svc: Services = state["services"]
        incident = await svc.data.incident(incident_id)
        if incident is None:
            return None
        async with lock:
            with RUN_SECONDS.time():
                result = await svc.investigator().investigate(incident)
        RUNS.labels(result.run.status, result.run.provider).inc()
        TOKENS.labels("input").inc(result.run.input_tokens)
        TOKENS.labels("output").inc(result.run.output_tokens)
        COST.inc(result.run.cost_usd)
        return result

    async def watch() -> None:
        svc: Services = state["services"]
        while True:
            try:
                incident_id = await svc.reader.next_uninvestigated(settings.watch_detector_run)
                if incident_id is not None:
                    result = await investigate(incident_id)
                    log.info("investigated %s: %s", incident_id, result and result.run.status)
                    continue
            except Exception:
                log.exception("watcher iteration failed")
            await asyncio.sleep(settings.watch_interval_s)

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        async with contextlib.AsyncExitStack() as stack:
            if state["services"] is None:
                from agent.runtime import open_runtime
                from agent.trace import PgTraceReader

                rt = await stack.enter_async_context(open_runtime(settings))
                state["services"] = Services(
                    rt.data, rt.approvals, PgTraceReader(rt.pool), rt.investigator, rt.decider
                )
            watcher = asyncio.create_task(watch()) if settings.watch else None
            try:
                yield
            finally:
                if watcher is not None:
                    watcher.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await watcher

    app = FastAPI(
        title="Incident Assistant: Agent API",
        version=SCHEMA_VERSION,
        description="Investigates incidents with tools and returns cited diagnoses. "
        "State-changing actions become approval requests that a person decides.",
        lifespan=lifespan,
    )
    app.state.settings = settings

    def svc() -> Services:
        s: Services = state["services"]
        return s

    @app.post(
        "/v1/incidents/{incident_id}/investigate",
        response_model=InvestigateResponse,
        summary="Run an investigation now (blocks until it finishes)",
    )
    async def run_investigation(incident_id: uuid.UUID, _: AgentAuth) -> InvestigateResponse:
        result = await investigate(incident_id)
        if result is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"No incident {incident_id}")
        return to_response(result)

    @app.get("/v1/incidents/{incident_id}/diagnosis", summary="Latest successful diagnosis")
    async def latest_diagnosis(incident_id: uuid.UUID, _: AgentAuth) -> dict[str, Any]:
        run = await svc().reader.latest_diagnosis(incident_id)
        if run is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No diagnosis for this incident yet")
        return run

    @app.get("/v1/runs/{run_id}", summary="A run with its full trace")
    async def get_run(run_id: uuid.UUID, _: AgentAuth) -> dict[str, Any]:
        run = await svc().reader.run(run_id)
        if run is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"No run {run_id}")
        return run

    @app.get("/v1/approvals", summary="Approval requests, newest first")
    async def list_approvals(
        _: AgentAuth,
        status_: Annotated[
            Literal["pending", "approved", "rejected"] | None, Query(alias="status")
        ] = None,
    ) -> list[dict[str, Any]]:
        return await svc().approvals.list(status_, 100)

    @app.post("/v1/approvals/{request_id}/decision", summary="Approve or reject a request")
    async def decide(
        request_id: uuid.UUID, body: DecisionRequest, _: ApproverAuth
    ) -> dict[str, Any]:
        decider = svc().decider
        if decider is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Approvals not configured")
        try:
            row = await decider.decide(
                request_id, body.decision == "approve", body.decided_by, body.note
            )
        except DecisionError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        APPROVALS.labels(body.decision).inc()
        return row

    @app.get("/healthz", response_model=HealthResponse, summary="Liveness check")
    async def healthz() -> HealthResponse:
        return HealthResponse()

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app


def app_factory() -> FastAPI:
    """uvicorn --factory entry point."""
    logging.basicConfig(level=logging.INFO)
    return create_app()
