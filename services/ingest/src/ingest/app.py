"""Telemetry ingestion API.

Validates and authenticates batches, drops events whose event_id was seen recently, and hands
the rest to a bounded sink. A full sink means 429 with Retry-After. A2 points the sink at Kafka.
"""

import asyncio
import contextlib
import secrets
from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, status
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader
from starlette.requests import Request

from incident_contracts import SCHEMA_VERSION, TelemetryBatch, TelemetryEvent
from incident_contracts.api import ErrorResponse, HealthResponse, IngestResponse
from ingest.dedupe import RecentIds
from ingest.settings import Settings
from ingest.sink import BufferedSink

API_KEY_HEADER = "X-API-Key"
_api_key_header = APIKeyHeader(name=API_KEY_HEADER, auto_error=False)


class ApiError(Exception):
    def __init__(
        self, status_code: int, error: str, message: str, headers: dict[str, str] | None = None
    ) -> None:
        self.status_code = status_code
        self.body = ErrorResponse(error=error, message=message)
        self.headers = headers


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def require_api_key(
    key: Annotated[str | None, Depends(_api_key_header)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> str:
    # compare_digest on every key so response time doesn't leak which prefix matched.
    if key is None or not any(secrets.compare_digest(key, k) for k in settings.api_keys):
        raise ApiError(status.HTTP_401_UNAUTHORIZED, "unauthorized", "Missing or invalid API key")
    return key


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    sink = BufferedSink(capacity=settings.queue_capacity)
    recent = RecentIds(ttl_s=settings.dedupe_ttl_s, max_ids=settings.dedupe_max_ids)

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        drain = asyncio.create_task(sink.run())
        yield
        drain.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await drain

    app = FastAPI(
        title="Incident Assistant: Telemetry Ingestion API",
        version=SCHEMA_VERSION,
        description="Accepts batched GPU telemetry: GPU metric samples, XID errors, and BMC log "
        "entries (Redfish LogEntry shape).",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.sink = sink
    app.state.recent_ids = recent

    @app.exception_handler(ApiError)
    async def _api_error_handler(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code, content=exc.body.model_dump(), headers=exc.headers
        )

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
        fresh: dict[UUID, TelemetryEvent] = {}
        for event in batch.events:
            if event.event_id not in recent and event.event_id not in fresh:
                fresh[event.event_id] = event
        # Only remember IDs once they're buffered; a 429'd batch must be retryable as-is.
        if not sink.try_put(list(fresh.values())):
            raise ApiError(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "overloaded",
                "Ingestion queue is full; retry later",
                headers={"Retry-After": str(settings.retry_after_s)},
            )
        recent.add_all(fresh)
        return IngestResponse(accepted=len(fresh), duplicates=len(batch.events) - len(fresh))

    @app.get("/healthz", response_model=HealthResponse, summary="Liveness check")
    async def healthz() -> HealthResponse:
        return HealthResponse()

    return app


app = create_app()
