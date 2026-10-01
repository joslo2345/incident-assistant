"""Wiring from environment variables to live dependencies (database pools, knowledge API,
model provider). Tests build the same objects from fakes instead."""

from __future__ import annotations

import contextlib
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import asyncpg
import httpx2

from agent.data import ApprovalDecider, HttpKnowledge, PgApprovals, PgFleetData
from agent.llm import Provider, provider_from_env
from agent.loop import Budget, Investigator
from agent.tools import Toolbox
from agent.trace import PgTraceStore

DEFAULT_DB = "postgresql://incident_agent@localhost:5432/telemetry"


@dataclass(frozen=True)
class Settings:
    database_url: str = DEFAULT_DB
    approval_database_url: str | None = None  # only the approvals endpoint/CLI need it
    knowledge_url: str = "http://localhost:8001"
    knowledge_api_key: str = ""
    api_keys: frozenset[str] = field(default_factory=frozenset)
    approver_keys: frozenset[str] = field(default_factory=frozenset)
    watch: bool = False
    watch_interval_s: float = 30.0
    watch_detector_run: str = "live"
    budget: Budget = field(default_factory=Budget)

    @classmethod
    def from_env(cls) -> Settings:
        def keys(name: str) -> frozenset[str]:
            return frozenset(k.strip() for k in os.environ.get(name, "").split(",") if k.strip())

        budget = Budget(
            max_steps=int(os.environ.get("AGENT_MAX_STEPS", Budget.max_steps)),
            max_tokens=int(os.environ.get("AGENT_MAX_TOKENS", Budget.max_tokens)),
            model_timeout_s=float(os.environ.get("AGENT_MODEL_TIMEOUT_S", Budget.model_timeout_s)),
            run_timeout_s=float(os.environ.get("AGENT_RUN_TIMEOUT_S", Budget.run_timeout_s)),
        )
        return cls(
            database_url=os.environ.get("DATABASE_URL", DEFAULT_DB),
            approval_database_url=os.environ.get("APPROVAL_DATABASE_URL") or None,
            knowledge_url=os.environ.get("KNOWLEDGE_URL", cls.knowledge_url),
            knowledge_api_key=os.environ.get("KNOWLEDGE_API_KEY", ""),
            api_keys=keys("AGENT_API_KEYS"),
            approver_keys=keys("APPROVER_API_KEYS"),
            watch=os.environ.get("AGENT_WATCH", "false").lower() in {"1", "true", "yes"},
            watch_interval_s=float(os.environ.get("AGENT_WATCH_INTERVAL_S", 30)),
            watch_detector_run=os.environ.get("AGENT_WATCH_RUN", "live"),
            budget=budget,
        )


@dataclass
class Runtime:
    settings: Settings
    pool: asyncpg.Pool[Any]
    data: PgFleetData
    approvals: PgApprovals
    toolbox: Toolbox
    traces: PgTraceStore
    provider: Provider
    decider: ApprovalDecider | None

    def investigator(self) -> Investigator:
        return Investigator(
            self.provider, self.toolbox, self.approvals, self.traces, self.settings.budget
        )


@contextlib.asynccontextmanager
async def open_runtime(
    settings: Settings | None = None, provider: Provider | None = None
) -> AsyncIterator[Runtime]:
    settings = settings or Settings.from_env()
    pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=4)
    approver_pool = (
        await asyncpg.create_pool(settings.approval_database_url, min_size=1, max_size=2)
        if settings.approval_database_url
        else None
    )
    http = httpx2.AsyncClient(
        base_url=settings.knowledge_url,
        headers={"X-API-Key": settings.knowledge_api_key},
        timeout=15.0,
    )
    try:
        data, approvals = PgFleetData(pool), PgApprovals(pool)
        yield Runtime(
            settings=settings,
            pool=pool,
            data=data,
            approvals=approvals,
            toolbox=Toolbox(data, HttpKnowledge(http), approvals),
            traces=PgTraceStore(pool),
            provider=provider or provider_from_env(),
            decider=ApprovalDecider(approver_pool) if approver_pool else None,
        )
    finally:
        await http.aclose()
        if approver_pool is not None:
            await approver_pool.close()
        await pool.close()
