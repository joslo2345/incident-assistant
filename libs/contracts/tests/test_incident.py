from typing import Any

import pytest
from pydantic import ValidationError

from incident_contracts import ActionType, Incident, RecommendedAction


def test_open_incident_without_diagnosis(incident: dict[str, Any]) -> None:
    parsed = Incident.model_validate(incident)
    assert parsed.diagnosis is None
    assert Incident.model_validate_json(parsed.model_dump_json()) == parsed


def test_diagnosed_incident(incident: dict[str, Any], diagnosis: dict[str, Any]) -> None:
    parsed = Incident.model_validate({**incident, "status": "diagnosed", "diagnosis": diagnosis})
    assert parsed.diagnosis is not None
    assert parsed.diagnosis.recommended_action.requires_approval


def test_diagnosed_status_requires_diagnosis(incident: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match="must include a diagnosis"):
        Incident.model_validate({**incident, "status": "diagnosed"})


def test_diagnosis_cannot_cite_unknown_evidence(
    incident: dict[str, Any], diagnosis: dict[str, Any]
) -> None:
    bad = {**diagnosis, "evidence_ids": ["ev-1", "ev-404"]}
    with pytest.raises(ValidationError, match="ev-404"):
        Incident.model_validate({**incident, "diagnosis": bad})


def test_incident_needs_evidence(incident: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Incident.model_validate({**incident, "evidence": []})


def test_evidence_window_must_be_ordered(incident: dict[str, Any]) -> None:
    ev = {**incident["evidence"][0], "window_end": "2026-10-01T11:00:00Z"}
    with pytest.raises(ValidationError, match="window_end"):
        Incident.model_validate({**incident, "evidence": [ev]})


def test_updated_at_not_before_created_at(incident: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match="updated_at"):
        Incident.model_validate({**incident, "updated_at": "2026-10-01T11:00:00Z"})


def test_confidence_bounds(incident: dict[str, Any], diagnosis: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Incident.model_validate({**incident, "diagnosis": {**diagnosis, "confidence": 1.5}})


@pytest.mark.parametrize(
    ("action", "needs_approval"),
    [
        (ActionType.DRAIN_NODE, True),
        (ActionType.RESET_GPU, True),
        (ActionType.REPLACE_PART, False),
        (ActionType.ESCALATE, False),
    ],
)
def test_only_state_changing_actions_need_approval(
    action: ActionType, needs_approval: bool
) -> None:
    rec = RecommendedAction(
        action=action,
        target={"kind": "node", "node_id": "gpu-node-01"},
        rationale="test",
    )
    assert rec.requires_approval is needs_approval


def test_targeted_action_requires_target() -> None:
    with pytest.raises(ValidationError, match="requires a target"):
        RecommendedAction(action=ActionType.DRAIN_NODE, rationale="no target")
    assert RecommendedAction(action=ActionType.MONITOR, rationale="watch it").target is None
