"""Telemetry ingestion API.

Validates and authenticates batches, drops events whose event_id was seen recently, and publishes
the rest to Kafka. It returns 202 only once Kafka has acknowledged every event. Too many events in
flight means 429; a Kafka failure means 503. Either way the client retries the identical batch.
"""

import contextlib
import logging
import secrets
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, Response, status
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from starlette.requests import Request

from incident_contracts import SCHEMA_VERSION, TelemetryBatch, TelemetryEvent
from incident_contracts.api import ErrorResponse, HealthResponse, IngestResponse
from ingest.dedupe import RecentIds
from ingest.limits import MaxBodySizeMiddleware
from ingest.publisher import KafkaPublisher, MemoryPublisher, Publisher, PublishError
from ingest.settings import Settings

log = logging.getLogger("ingest")

API_KEY_HEADER = "X-API-Key"
_api_key_header = APIKeyHeader(name=API_KEY_HEADER, auto_error=False)

REQUESTS = Counter("ingest_requests_total", "HTTP requests", ["path", "status"])
REQUEST_SECONDS = Histogram("ingest_request_seconds", "HTTP request latency", ["path"])
EVENTS = Counter("ingest_events_total", "Events received", ["result"])
PUBLISH_SECONDS = Histogram("ingest_publish_seconds", "Time for Kafka to acknowledge a batch")
IN_FLIGHT = Gauge("ingest_in_flight_events", "Events being published right now")


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


def create_app(settings: Settings | None = None, publisher: Publisher | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    if publisher is None:
        publisher = (
            KafkaPublisher(settings.kafka_bootstrap, settings.kafka_topic)
            if settings.kafka_bootstrap
            else MemoryPublisher()
        )
    recent = RecentIds(ttl_s=settings.dedupe_ttl_s, max_ids=settings.dedupe_max_ids)
    in_flight = 0
    retry_headers = {"Retry-After": str(settings.retry_after_s)}

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await publisher.start()
        yield
        await publisher.stop()

    app = FastAPI(
        title="Incident Assistant: Telemetry Ingestion API",
        version=SCHEMA_VERSION,
        description="Accepts batched GPU telemetry: GPU metric samples, XID errors, and BMC log "
        "entries (Redfish LogEntry shape).",
        lifespan=lifespan,
    )
    app.add_middleware(MaxBodySizeMiddleware, max_bytes=settings.max_body_bytes)
    app.state.settings = settings
    app.state.publisher = publisher
    app.state.recent_ids = recent

    @app.middleware("http")
    async def _metrics(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        started = time.perf_counter()
        response = await call_next(request)
        route = request.scope.get("route")
        path = getattr(route, "path", "unmatched")
        REQUEST_SECONDS.labels(path).observe(time.perf_counter() - started)
        REQUESTS.labels(path, str(response.status_code)).inc()
        return response

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
            413: {"model": ErrorResponse, "description": "Request body too large"},
            503: {
                "model": ErrorResponse,
                "description": "Stream unavailable; retry after the number of seconds in "
                "Retry-After",
            },
        },
        summary="Ingest a batch of telemetry events",
        description="202 means every new event is durably stored in the stream.",
    )
    async def ingest_telemetry(
        batch: TelemetryBatch, _: Annotated[str, Depends(require_api_key)]
    ) -> IngestResponse:
        nonlocal in_flight
        fresh: dict[UUID, TelemetryEvent] = {}
        for event in batch.events:
            if event.event_id not in recent and event.event_id not in fresh:
                fresh[event.event_id] = event
        duplicates = len(batch.events) - len(fresh)

        if fresh:
            if in_flight + len(fresh) > settings.max_in_flight:
                raise ApiError(
                    status.HTTP_429_TOO_MANY_REQUESTS,
                    "overloaded",
                    "Too many events in flight; retry later",
                    headers=retry_headers,
                )
            in_flight += len(fresh)
            IN_FLIGHT.set(in_flight)
            started = time.perf_counter()
            try:
                await publisher.publish(list(fresh.values()))
            except PublishError as exc:
                # Nothing is remembered, so the retried batch is accepted in full. Events that did
                # reach Kafka before the failure are deduplicated by the consumer's insert.
                # Broker details go to the log, not to the client.
                log.warning("publish failed: %s", exc)
                raise ApiError(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    "unavailable",
                    "Telemetry stream unavailable; retry later",
                    headers=retry_headers,
                ) from exc
            finally:
                in_flight -= len(fresh)
                IN_FLIGHT.set(in_flight)
            PUBLISH_SECONDS.observe(time.perf_counter() - started)
            recent.add_all(fresh)

        EVENTS.labels("accepted").inc(len(fresh))
        EVENTS.labels("duplicate").inc(duplicates)
        return IngestResponse(accepted=len(fresh), duplicates=duplicates)

    @app.get("/healthz", response_model=HealthResponse, summary="Liveness check")
    async def healthz() -> HealthResponse:
        return HealthResponse()

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app


app = create_app()
