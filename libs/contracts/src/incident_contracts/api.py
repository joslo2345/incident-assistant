"""Response bodies for the HTTP APIs. Request bodies live next to their domain models."""

from typing import Literal

from pydantic import Field

from incident_contracts.common import Contract, FailureType
from incident_contracts.incident import Citation


class IngestResponse(Contract):
    """202 response for POST /v1/telemetry."""

    accepted: int = Field(ge=0, description="Events queued for processing")
    duplicates: int = Field(
        ge=0, description="Events skipped because their event_id was already seen"
    )


class ErrorResponse(Contract):
    """Body for 4xx/5xx errors other than request validation (422)."""

    error: str = Field(description='Machine-readable code, e.g. "unauthorized", "overloaded"')
    message: str


class HealthResponse(Contract):
    status: Literal["ok"] = "ok"


# Knowledge service (A4) -------------------------------------------------------------------------


class SearchRequest(Contract):
    """Body for POST /v1/search and the retrieval part of POST /v1/ask."""

    query: str = Field(min_length=3, max_length=2000)
    k: int = Field(default=5, ge=1, le=20)
    failure_type: FailureType | None = Field(
        default=None, description="Only chunks tagged with this failure type"
    )
    doc_kind: Literal["runbook", "incident"] | None = None


class SourceChunk(Contract):
    chunk_id: str
    doc_id: str
    doc_kind: Literal["runbook", "incident"]
    title: str
    heading: str
    text: str
    score: float = Field(description="Reranker score if reranked, otherwise fused rank score")
    url: str = Field(description="GET path that returns this chunk")


class SearchResponse(Contract):
    query: str
    hits: list[SourceChunk]


class AskRequest(SearchRequest):
    """Body for POST /v1/ask."""


class AnswerClaim(Contract):
    text: str
    citations: list[Citation] = Field(min_length=1)


class AskResponse(Contract):
    """A grounded answer: every claim cites the chunks it came from, or the answer says it can't."""

    question: str
    answer: str
    insufficient_information: bool
    claims: list[AnswerClaim]
    sources: list[SourceChunk]
    provider: str = Field(description='"claude", or "extractive" when no model is configured')
    model: str | None = None
