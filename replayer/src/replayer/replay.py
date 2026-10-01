"""Sends simulated telemetry to the ingest API at a controlled speed."""

import asyncio
import random
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass

import httpx2

from incident_contracts import MAX_BATCH_SIZE, TelemetryBatch, TelemetryEvent
from replayer.synth import FleetSimulator

API_KEY_HEADER = "X-API-Key"


class ReplayError(Exception):
    pass


@dataclass
class ReplayStats:
    sim_seconds: float = 0.0
    wall_seconds: float = 0.0
    batches: int = 0
    events_sent: int = 0
    accepted: int = 0
    duplicates: int = 0
    retries_429: int = 0
    retries_error: int = 0

    @property
    def events_per_second(self) -> float:
        return self.events_sent / self.wall_seconds if self.wall_seconds else 0.0

    def as_dict(self) -> dict[str, float]:
        return {**asdict(self), "events_per_second": round(self.events_per_second, 1)}


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 8
    base_backoff_s: float = 0.25
    max_backoff_s: float = 10.0

    def backoff(self, attempt: int) -> float:
        """Exponential backoff with full jitter."""
        return random.uniform(0, min(self.max_backoff_s, self.base_backoff_s * 2**attempt))


class Sender:
    def __init__(
        self, client: httpx2.AsyncClient, stats: ReplayStats, retry: RetryPolicy | None = None
    ) -> None:
        self._client = client
        self._stats = stats
        self._retry = retry or RetryPolicy()

    async def send(self, events: Sequence[TelemetryEvent]) -> None:
        """POST one batch, retrying 429s and transient failures with the same event IDs."""
        body = TelemetryBatch(events=list(events)).model_dump_json()
        headers = {"Content-Type": "application/json"}
        for attempt in range(self._retry.max_attempts):
            try:
                resp = await self._client.post("/v1/telemetry", content=body, headers=headers)
            except httpx2.TransportError:
                self._stats.retries_error += 1
                await asyncio.sleep(self._retry.backoff(attempt))
                continue
            if resp.status_code == 202:
                result = resp.json()
                self._stats.batches += 1
                self._stats.events_sent += len(events)
                self._stats.accepted += result["accepted"]
                self._stats.duplicates += result["duplicates"]
                return
            if resp.status_code == 429:
                self._stats.retries_429 += 1
                wait = float(resp.headers.get("Retry-After", 1))
                await asyncio.sleep(wait + random.uniform(0, wait / 2))
                continue
            if resp.status_code >= 500:
                self._stats.retries_error += 1
                await asyncio.sleep(self._retry.backoff(attempt))
                continue
            # 401/422 and similar won't succeed on retry.
            raise ReplayError(f"ingest rejected batch: {resp.status_code} {resp.text[:500]}")
        raise ReplayError(f"batch failed after {self._retry.max_attempts} attempts")


async def replay(
    sim: FleetSimulator,
    client: httpx2.AsyncClient,
    duration_s: float,
    speed: float,
    batch_size: int = 500,
    concurrency: int = 4,
) -> ReplayStats:
    """Replay `duration_s` of simulated time. speed=10 means 10x real time; speed=0 means as
    fast as the API accepts (for throughput tests)."""
    if not 1 <= batch_size <= MAX_BATCH_SIZE:
        raise ValueError(f"batch_size must be between 1 and {MAX_BATCH_SIZE}")
    stats = ReplayStats()
    sender = Sender(client, stats)
    slots = asyncio.Semaphore(concurrency)
    in_flight: set[asyncio.Task[None]] = set()
    failures: list[BaseException] = []
    pending: list[TelemetryEvent] = []

    def on_done(task: asyncio.Task[None]) -> None:
        in_flight.discard(task)
        slots.release()
        if not task.cancelled() and (exc := task.exception()) is not None:
            failures.append(exc)

    async def dispatch(events: list[TelemetryEvent]) -> None:
        await slots.acquire()
        if failures:
            slots.release()
            raise failures[0]
        task = asyncio.create_task(sender.send(events))
        in_flight.add(task)
        task.add_done_callback(on_done)

    started = time.monotonic()
    for t, events in sim.steps(duration_s):
        if speed > 0:
            # Don't send a sample before its (scaled) time has come.
            delay = t / speed - (time.monotonic() - started)
            if delay > 0:
                await asyncio.sleep(delay)
        pending.extend(events)
        while len(pending) >= batch_size:
            await dispatch(pending[:batch_size])
            pending = pending[batch_size:]
        if speed > 0 and pending:
            # In real-time mode, flush every interval so data isn't held back.
            await dispatch(pending)
            pending = []
    if pending:
        await dispatch(pending)
    await asyncio.gather(*in_flight, return_exceptions=True)
    if failures:
        raise failures[0]
    stats.sim_seconds = duration_s
    stats.wall_seconds = time.monotonic() - started
    return stats
