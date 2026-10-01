"""Incident records: created by the detector (A3), diagnosed by the agent (A5).

Lifecycle: open -> investigating -> diagnosed -> resolved | dismissed.
The detector fills in everything except `diagnosis`. The agent adds the diagnosis.
"""

from enum import StrEnum
from typing import Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from incident_contracts.common import SCHEMA_VERSION, Contract, FailureType, GpuIndex, NodeId


class IncidentStatus(StrEnum):
    OPEN = "open"
    INVESTIGATING = "investigating"
    DIAGNOSED = "diagnosed"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


class Severity(StrEnum):
    SEV1 = "sev1"  # GPU or node down, jobs failing
    SEV2 = "sev2"  # degraded, failure likely soon
    SEV3 = "sev3"  # anomaly worth a look
    SEV4 = "sev4"  # informational


class ComponentKind(StrEnum):
    GPU = "gpu"
    NODE = "node"
    FAN = "fan"
    PSU = "psu"
    NVLINK = "nvlink"
    PCIE = "pcie"


class ComponentRef(Contract):
    kind: ComponentKind
    node_id: NodeId
    gpu_index: GpuIndex | None = None
    name: str | None = Field(default=None, max_length=128, description='e.g. "FAN3", "PSU1"')


class DetectorKind(StrEnum):
    STATIC_THRESHOLD = "static_threshold"
    ZSCORE = "zscore"
    EVENT_MATCH = "event_match"  # e.g. an XID or a critical BMC line
    MODEL = "model"  # e.g. Isolation Forest


class Evidence(Contract):
    """One signal that contributed to the incident, with the window it was seen in."""

    evidence_id: str = Field(min_length=1, max_length=64)
    detector: DetectorKind
    component: ComponentRef
    signal: str = Field(max_length=128, description='Metric or event name, e.g. "temperature_c"')
    window_start: AwareDatetime
    window_end: AwareDatetime
    observed_value: float | None = None
    threshold: float | None = None
    score: float | None = Field(default=None, description="z-score or model score, if any")
    description: str = Field(max_length=1000)

    @model_validator(mode="after")
    def _window_ordered(self) -> Self:
        if self.window_end < self.window_start:
            raise ValueError("window_end must not be before window_start")
        return self


class Citation(Contract):
    """Pointer to a knowledge-base chunk (A4) that supports a claim."""

    chunk_id: str = Field(min_length=1, max_length=128)
    doc_id: str = Field(min_length=1, max_length=128)
    quote: str | None = Field(default=None, max_length=1000)


class ActionType(StrEnum):
    NONE = "none"
    MONITOR = "monitor"
    RESET_GPU = "reset_gpu"
    DRAIN_NODE = "drain_node"
    REPLACE_PART = "replace_part"
    ESCALATE = "escalate"


# Actions that change fleet state and must go through human approval.
STATE_CHANGING_ACTIONS = frozenset({ActionType.RESET_GPU, ActionType.DRAIN_NODE})


class RecommendedAction(Contract):
    action: ActionType
    target: ComponentRef | None = None
    rationale: str = Field(max_length=2000)

    @property
    def requires_approval(self) -> bool:
        return self.action in STATE_CHANGING_ACTIONS

    @model_validator(mode="after")
    def _target_when_needed(self) -> Self:
        if self.action not in {ActionType.NONE, ActionType.MONITOR} and self.target is None:
            raise ValueError(f"action '{self.action}' requires a target")
        return self


class Diagnosis(Contract):
    """Structured output of the investigation agent."""

    root_cause: FailureType
    confidence: float = Field(ge=0, le=1)
    summary: str = Field(max_length=4000)
    evidence_ids: list[str] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    recommended_action: RecommendedAction
    model: str = Field(max_length=128, description="Model that produced the diagnosis")
    trace_id: UUID | None = None
    created_at: AwareDatetime


class Incident(Contract):
    incident_id: UUID
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    created_at: AwareDatetime
    updated_at: AwareDatetime
    status: IncidentStatus
    severity: Severity
    title: str = Field(min_length=1, max_length=200)
    suspected_failure_type: FailureType = FailureType.UNKNOWN
    components: list[ComponentRef] = Field(min_length=1)
    evidence: list[Evidence] = Field(min_length=1)
    diagnosis: Diagnosis | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not be before created_at")
        if self.status == IncidentStatus.DIAGNOSED and self.diagnosis is None:
            raise ValueError("a diagnosed incident must include a diagnosis")
        if self.diagnosis is not None:
            known = {e.evidence_id for e in self.evidence}
            unknown = set(self.diagnosis.evidence_ids) - known
            if unknown:
                raise ValueError(f"diagnosis references unknown evidence ids: {sorted(unknown)}")
        return self
