"""Telemetry ingestion API.

A0 defines the HTTP contract only. A1 adds persistent idempotency and backpressure (429), and
A2 publishes accepted events to Kafka.
"""

import os
import secrets
from typing import Annotated

from fastapi import Depends, FastAPI, status
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader
from starlette.requests import Request

from incident_contracts import SCHEMA_VERSION, TelemetryBatch
from incident_contracts.api import ErrorResponse, HealthResponse, IngestResponse

API_KEY_HEADER = "X-API-Key"
_api_key_header = APIKeyHeader(name=API_KEY_HEADER, auto_error=False)


class ApiError(Exception):
    def __init__(self, status_code: int, error: str, message: str) -> None:
        self.status_code = status_code
        self.body = ErrorResponse(error=error, message=message)


def configured_api_keys() -> frozenset[str]:
    """Valid keys come from INGEST_API_KEYS (comma-separated). None configured = reject all."""
    raw = os.environ.get("INGEST_API_KEYS", "")
    return frozenset(k.strip() for k in raw.split(",") if k.strip())


def require_api_key(
    key: Annotated[str | None, Depends(_api_key_header)],
    valid_keys: Annotated[frozenset[str], Depends(configured_api_keys)],
) -> str:
    # compare_digest on every key so response time doesn't leak which prefix matched.
    if key is None or not any(secrets.compare_digest(key, k) for k in valid_keys):
        raise ApiError(status.HTTP_401_UNAUTHORIZED, "unauthorized", "Missing or invalid API key")
    return key


app = FastAPI(
    title="Incident Assistant: Telemetry Ingestion API",
    version=SCHEMA_VERSION,
    description="Accepts batched GPU telemetry: GPU metric samples, XID errors, and BMC log "
    "entries (Redfish LogEntry shape).",
)


@app.exception_handler(ApiError)
async def _api_error_handler(_: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.body.model_dump())


@app.post(
    "/v1/telemetry",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=IngestResponse,
    responses={
        401: {"model": ErrorResponse, "description": "Missing or invalid API key"},
        429: {
            "model": ErrorResponse,
            "description": "Overloaded; retry after the number of seconds in Retry-After",
        },
    },
    summary="Ingest a batch of telemetry events",
)
async def ingest_telemetry(
    batch: TelemetryBatch, _: Annotated[str, Depends(require_api_key)]
) -> IngestResponse:
    # Placeholder: dedupe within the batch only. A1 adds dedupe across batches.
    unique = {event.event_id for event in batch.events}
    return IngestResponse(accepted=len(unique), duplicates=len(batch.events) - len(unique))


@app.get("/healthz", response_model=HealthResponse, summary="Liveness check")
async def healthz() -> HealthResponse:
    return HealthResponse()
