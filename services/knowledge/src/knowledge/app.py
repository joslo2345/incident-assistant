"""Knowledge API: search the runbooks and past incidents, and answer questions with citations."""

from __future__ import annotations

import contextlib
import os
import secrets
import time
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Annotated, Any, Protocol
from urllib.parse import quote

import asyncpg
from fastapi import Depends, FastAPI, HTTPException, Response, status
from fastapi.security import APIKeyHeader
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.requests import Request

from incident_contracts import SCHEMA_VERSION
from incident_contracts.api import (
    AskRequest,
    AskResponse,
    HealthResponse,
    SearchRequest,
    SearchResponse,
    SourceChunk,
)
from knowledge.answer import NOT_ENOUGH, Answerer, answer_with_fallback, default_answerer
from knowledge.search import Hit, Mode, Searcher
from knowledge.store import setup_connection

API_KEY_HEADER = "X-API-Key"
_api_key_header = APIKeyHeader(name=API_KEY_HEADER, auto_error=False)

ASKS = Counter("knowledge_asks_total", "Questions answered", ["provider", "outcome"])
LATENCY = Histogram("knowledge_request_seconds", "Request latency", ["endpoint"])

# Cross-encoder (ms-marco MiniLM) logit below which the best passage is treated as unrelated.
# Calibrated on eval/retrieval/questions.jsonl (see eval/reports/retrieval.md).
DEFAULT_MIN_RELEVANCE = -0.4


@dataclass(frozen=True)
class Settings:
    api_keys: frozenset[str] = field(default_factory=frozenset)
    database_url: str = "postgresql://knowledge_service@localhost:5432/telemetry"
    min_relevance: float = DEFAULT_MIN_RELEVANCE
    mode: Mode = "rerank"

    @classmethod
    def from_env(cls) -> Settings:
        keys = os.environ.get("KNOWLEDGE_API_KEYS", "")
        return cls(
            api_keys=frozenset(k.strip() for k in keys.split(",") if k.strip()),
            database_url=os.environ.get("DATABASE_URL", cls.database_url),
            min_relevance=float(os.environ.get("KNOWLEDGE_MIN_RELEVANCE", DEFAULT_MIN_RELEVANCE)),
        )


class KnowledgeBase(Protocol):
    async def search(self, req: SearchRequest, mode: Mode) -> list[Hit]: ...
    async def get_chunk(self, chunk_id: str) -> Hit | None: ...


class PgKnowledgeBase:
    def __init__(self, pool: asyncpg.Pool[Any], searcher: Searcher) -> None:
        self.pool = pool
        self.searcher = searcher

    async def search(self, req: SearchRequest, mode: Mode) -> list[Hit]:
        async with self.pool.acquire() as conn:
            return await self.searcher.search(
                conn, req.query, req.k, mode,
                failure_type=req.failure_type.value if req.failure_type else None,
                doc_kind=req.doc_kind,
            )  # fmt: skip

    async def get_chunk(self, chunk_id: str) -> Hit | None:
        async with self.pool.acquire() as conn:
            r = await conn.fetchrow(
                "SELECT chunk_id, doc_id, doc_kind, title, heading, text, 0.0::float8 AS score "
                "FROM kb_chunks WHERE chunk_id = $1",
                chunk_id,
            )
        return Hit(**dict(r)) if r else None


def require_api_key(request: Request, key: Annotated[str | None, Depends(_api_key_header)]) -> str:
    settings: Settings = request.app.state.settings
    if key is None or not any(secrets.compare_digest(key, k) for k in settings.api_keys):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing or invalid API key")
    return key


Auth = Annotated[str, Depends(require_api_key)]


def to_source(h: Hit) -> SourceChunk:
    return SourceChunk(
        chunk_id=h.chunk_id,
        doc_id=h.doc_id,
        doc_kind=h.doc_kind,
        title=h.title,
        heading=h.heading,
        text=h.text,
        score=round(h.score, 4),
        # Chunk IDs contain '#', which would start a URL fragment; encode it.
        url=f"/v1/chunks/{quote(h.chunk_id, safe='')}",
    )


def create_app(
    settings: Settings | None = None,
    kb: KnowledgeBase | None = None,
    answerer: Answerer | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    state: dict[str, Any] = {"kb": kb, "answerer": answerer or default_answerer()}

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        pool = None
        if state["kb"] is None:  # real deployment: load models and connect
            from knowledge.models import FastEmbedEmbedder, FastEmbedReranker

            pool = await asyncpg.create_pool(
                settings.database_url, min_size=1, max_size=4, init=setup_connection
            )
            state["kb"] = PgKnowledgeBase(pool, Searcher(FastEmbedEmbedder(), FastEmbedReranker()))
        yield
        if pool is not None:
            await pool.close()

    app = FastAPI(
        title="Incident Assistant: Knowledge API",
        version=SCHEMA_VERSION,
        description="Hybrid search over runbooks and past incidents, and grounded answers in "
        "which every claim cites the chunks it came from.",
        lifespan=lifespan,
    )
    app.state.settings = settings

    def knowledge_base() -> KnowledgeBase:
        kb_: KnowledgeBase = state["kb"]
        return kb_

    @app.post("/v1/search", response_model=SearchResponse, summary="Search the knowledge base")
    async def search(req: SearchRequest, _: Auth) -> SearchResponse:
        with LATENCY.labels("search").time():
            hits = await knowledge_base().search(req, settings.mode)
        return SearchResponse(query=req.query, hits=[to_source(h) for h in hits])

    @app.post("/v1/ask", response_model=AskResponse, summary="Answer a question with citations")
    async def ask(req: AskRequest, _: Auth) -> AskResponse:
        started = time.perf_counter()
        hits = await knowledge_base().search(req, settings.mode)
        sources = [to_source(h) for h in hits]
        if gate_rejects(hits, settings):
            ASKS.labels("gate", "insufficient").inc()
            LATENCY.labels("ask").observe(time.perf_counter() - started)
            return AskResponse(
                question=req.query, answer=NOT_ENOUGH, insufficient_information=True,
                claims=[], sources=sources, provider="gate",
            )  # fmt: skip
        result = await answer_with_fallback(state["answerer"], req.query, hits)
        outcome = "insufficient" if result.insufficient_information else "answered"
        ASKS.labels(result.provider, outcome).inc()
        LATENCY.labels("ask").observe(time.perf_counter() - started)
        return AskResponse(
            question=req.query, answer=result.answer,
            insufficient_information=result.insufficient_information, claims=result.claims,
            sources=sources, provider=result.provider, model=result.model,
        )  # fmt: skip

    @app.get("/v1/chunks/{chunk_id}", response_model=SourceChunk, summary="Fetch a cited chunk")
    async def get_chunk(chunk_id: str, _: Auth) -> SourceChunk:
        hit = await knowledge_base().get_chunk(chunk_id)
        if hit is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"No chunk {chunk_id!r}")
        return to_source(hit)

    @app.get("/healthz", response_model=HealthResponse, summary="Liveness check")
    async def healthz() -> HealthResponse:
        return HealthResponse()

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app


def gate_rejects(hits: Sequence[Hit], settings: Settings) -> bool:
    """No hits, or (when reranking) even the best one is below the relevance threshold."""
    if not hits:
        return True
    return settings.mode == "rerank" and hits[0].score < settings.min_relevance


def app_factory() -> FastAPI:
    """uvicorn --factory entry point (builds the app at startup, not import)."""
    return create_app()
