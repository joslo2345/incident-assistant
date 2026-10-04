"""Groups alerts into incidents and classifies them.

Grouping: an alert joins an open incident on the same node that had a signal in the last
`join_window` and is related: the alert is node-level (fan, PSU), the incident has no GPUs yet,
they share a GPU, or the alert points at the same failure type (e.g. thermal throttling on the
other GPUs of a cooling zone). Otherwise it opens a new incident, so unrelated faults on
different GPUs of one node stay separate. Informational alerts (sev4) only open an incident
for workload shifts (so they're visible, labelled noisy_neighbor); inlet warnings and minor BMC
noise only attach to an existing incident. An incident resolves after `quiet_window` without alerts.

`opened_at` is when the incident first became actionable (sev1-3), which is what time-to-detect
measures. Informational incidents use their first alert time.
"""

from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from detector.rules import Alert
from incident_contracts import (
    ComponentKind,
    ComponentRef,
    Evidence,
    FailureType,
    Incident,
    IncidentStatus,
    Severity,
)

INCIDENT_NAMESPACE = uuid.UUID("5b0f6a8e-3c1d-4f2a-9e57-0d6c2b8a1f43")

# Checked in order: the first group with a matching alert decides the type.
CLASSIFICATION: list[tuple[FailureType, frozenset[str]]] = [
    (FailureType.GPU_OFF_BUS, frozenset({"xid_79", "gpu_missing", "pcie_fatal"})),
    (FailureType.ECC_DEGRADATION,
     frozenset({"xid_48", "ecc_dbe", "ecc_sbe_burst", "ecc_sbe", "retired_pages"})),
    (FailureType.POWER_FAULT, frozenset({"psu_fault", "power_limit_reduced"})),
    (FailureType.THERMAL_RUNAWAY,
     frozenset({"fan_failure", "thermal_throttle", "temp_high", "thermal_residual", "z_temp"})),
    (FailureType.NVLINK_DEGRADATION, frozenset({"xid_74", "nvlink_errors"})),
    (FailureType.PCIE_DEGRADATION, frozenset({"pcie_replays"})),
    (FailureType.NOISY_NEIGHBOR, frozenset({"workload_shift"})),
]  # fmt: skip
NEVER_OPENS = frozenset({"inlet_warning", "bmc_warning"})
SEVERITY_ORDER = [Severity.SEV1, Severity.SEV2, Severity.SEV3, Severity.SEV4]
MAX_EVIDENCE_PER_KIND = 3


def alert_order(a: Alert) -> tuple[object, ...]:
    return (a.time, a.node_id, -1 if a.gpu_index is None else a.gpu_index, a.kind, a.signal)


def classify(kinds: set[str]) -> FailureType:
    for failure_type, signals in CLASSIFICATION:
        if kinds & signals:
            return failure_type
    return FailureType.UNKNOWN


@dataclass
class OpenIncident:
    incident_id: uuid.UUID
    node_id: str
    first_signal_at: datetime
    last_signal_at: datetime
    opened_at: datetime | None = None  # first actionable alert
    alerts: list[Alert] = field(default_factory=list)
    status: IncidentStatus = IncidentStatus.OPEN
    updated_at: datetime | None = None

    @property
    def kinds(self) -> set[str]:
        return {a.kind for a in self.alerts}

    @property
    def severity(self) -> Severity:
        return min((a.severity for a in self.alerts), key=SEVERITY_ORDER.index)

    @property
    def failure_type(self) -> FailureType:
        return classify(self.kinds)

    @property
    def actionable(self) -> bool:
        return self.opened_at is not None

    @property
    def gpus(self) -> list[int]:
        return sorted({a.gpu_index for a in self.alerts if a.gpu_index is not None})

    def to_contract(self) -> Incident:
        ftype = self.failure_type
        node = ComponentRef(kind=ComponentKind.NODE, node_id=self.node_id)
        components = [node] + [
            ComponentRef(kind=ComponentKind.GPU, node_id=self.node_id, gpu_index=g)
            for g in self.gpus
        ]
        evidence = []
        per_kind: Counter[str] = Counter()
        for i, a in enumerate(self.alerts):
            per_kind[a.kind] += 1
            if per_kind[a.kind] > MAX_EVIDENCE_PER_KIND:
                continue
            component = (
                ComponentRef(kind=ComponentKind.GPU, node_id=a.node_id, gpu_index=a.gpu_index)
                if a.gpu_index is not None
                else node
            )
            evidence.append(
                Evidence(
                    evidence_id=f"ev-{i}",
                    detector=a.detector,
                    component=component,
                    signal=a.signal,
                    window_start=a.time,
                    window_end=a.time + timedelta(minutes=1),
                    observed_value=a.value,
                    threshold=a.threshold,
                    score=a.score,
                    description=f"[{a.kind}] {a.description}"[:1000],
                )
            )
        gpu_text = f" GPU {', '.join(map(str, self.gpus))}" if self.gpus else ""
        return Incident(
            incident_id=self.incident_id,
            created_at=self.opened_at or self.first_signal_at,
            updated_at=self.updated_at or self.last_signal_at,
            status=self.status,
            severity=Severity.SEV4 if ftype == FailureType.NOISY_NEIGHBOR else self.severity,
            title=f"{ftype.value.replace('_', ' ').capitalize()} on {self.node_id}{gpu_text}",
            suspected_failure_type=ftype,
            components=components,
            evidence=evidence,
        )


class IncidentTracker:
    def __init__(
        self,
        run: str,
        join_window: timedelta = timedelta(minutes=20),
        quiet_window: timedelta = timedelta(minutes=30),
    ) -> None:
        self.run = run
        self.join_window = join_window
        self.quiet_window = quiet_window
        self.open: dict[str, list[OpenIncident]] = {}

    def update(self, minute: datetime, alerts: list[Alert]) -> list[OpenIncident]:
        """Apply this minute's alerts; return incidents that changed (including resolved)."""
        changed: dict[uuid.UUID, OpenIncident] = {}
        # Ties broken on every field, so the same telemetry always yields the same evidence IDs
        # (alerts within one minute used to come out in production order; the A6 CI eval found it).
        for a in sorted(alerts, key=alert_order):
            inc = self._related(a)
            if inc is None:
                if a.kind in NEVER_OPENS:
                    continue
                inc = OpenIncident(
                    # Deterministic, so re-processing the same data upserts instead of duplicating.
                    incident_id=uuid.uuid5(
                        INCIDENT_NAMESPACE,
                        f"{self.run}/{a.node_id}/{a.gpu_index}/{a.kind}/{a.time}",
                    ),
                    node_id=a.node_id,
                    first_signal_at=a.time,
                    last_signal_at=a.time,
                )
                self.open.setdefault(a.node_id, []).append(inc)
            inc.alerts.append(a)
            inc.last_signal_at = max(inc.last_signal_at, a.time)
            # An alert can carry a time inside the minute (a BMC entry at 01:43:26 while minute
            # 01:43:00 is processed); updated_at must not fall before the incident's created_at.
            inc.updated_at = max(minute, a.time)
            if inc.opened_at is None and a.severity != Severity.SEV4:
                inc.opened_at = a.time
            changed[inc.incident_id] = inc
        for node_id, incs in list(self.open.items()):
            for inc in list(incs):
                if minute - inc.last_signal_at > self.quiet_window:
                    inc.status = IncidentStatus.RESOLVED
                    inc.updated_at = minute
                    changed[inc.incident_id] = inc
                    incs.remove(inc)
            if not incs:
                del self.open[node_id]
        return list(changed.values())

    def _related(self, a: Alert) -> OpenIncident | None:
        """The most recently active open incident this alert belongs to, if any."""
        family = classify({a.kind})
        candidates = [
            inc
            for inc in self.open.get(a.node_id, [])
            if a.time - inc.last_signal_at <= self.join_window
            and (
                a.gpu_index is None
                or not inc.gpus
                or a.gpu_index in inc.gpus
                or (family != FailureType.UNKNOWN and family == inc.failure_type)
            )
        ]
        return max(candidates, key=lambda i: i.last_signal_at, default=None)

    def close_all(self, minute: datetime) -> list[OpenIncident]:
        """End of a backfill: report what's still open (it stays open)."""
        return [inc for incs in self.open.values() for inc in incs]
