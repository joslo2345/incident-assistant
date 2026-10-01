"""Response bodies for the HTTP APIs. Request bodies live next to their domain models."""

from typing import Literal

from pydantic import Field

from incident_contracts.common import Contract


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
