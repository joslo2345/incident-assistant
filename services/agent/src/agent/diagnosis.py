"""The structured output of an investigation, and the checks it must pass.

The model submits its diagnosis by calling `submit_diagnosis`, which works the same way with
every provider. The arguments are checked against the schema and against the run itself: evidence
IDs must exist on the incident, and citations must be chunks a tool actually returned in this run.
Failures go back to the model as a tool error so it can correct them (bounded retries).
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, Field, ValidationError

from agent.llm import ToolSpec
from agent.tools import Args, ToolContext, json_schema
from incident_contracts import (
    ActionType,
    Citation,
    ComponentKind,
    ComponentRef,
    Diagnosis,
    FailureType,
    Incident,
    RecommendedAction,
)

SUBMIT = "submit_diagnosis"


class SubmitDiagnosis(Args):
    root_cause: FailureType = Field(
        description="noisy_neighbor when it's workload behaviour, not a hardware fault; unknown "
        "when the evidence doesn't support any cause"
    )
    confidence: float = Field(ge=0, le=1, description="0-1: how sure you are of the root cause")
    summary: str = Field(
        min_length=20, max_length=4000,
        description="What happened and why you believe it, citing the observations you made",
    )  # fmt: skip
    evidence_ids: list[str] = Field(
        max_length=20, description="IDs of the incident's evidence items that support the cause"
    )
    citations: list[str] = Field(
        max_length=10,
        description="chunk_ids of runbook or past-incident passages returned by the tools that "
        "support the diagnosis or the action",
    )
    action: ActionType = Field(
        description="drain_node and reset_gpu go to a human for approval before anything happens"
    )
    action_gpu_index: int | None = Field(
        default=None, ge=0, lt=16, description="The GPU the action targets, if it targets one GPU"
    )
    action_rationale: str = Field(min_length=10, max_length=2000)


SUBMIT_SPEC = ToolSpec(
    SUBMIT,
    "Submit the final diagnosis. Call this exactly once, when the investigation is complete; the "
    "run ends when it is accepted.",
    json_schema(SubmitDiagnosis),
)


def check(
    arguments: dict[str, object] | None,
    incident: Incident,
    ctx: ToolContext,
    model: str,
    run_id: uuid.UUID,
) -> tuple[Diagnosis | None, list[str]]:
    """Return (diagnosis, []) when valid, or (None, problems) to send back to the model."""
    if arguments is None:
        return None, ["arguments were not valid JSON"]
    try:
        sub = SubmitDiagnosis.model_validate(arguments)
    except ValidationError as exc:
        return None, [
            f"{'.'.join(map(str, e['loc'])) or 'arguments'}: {e['msg']}" for e in exc.errors()
        ]
    problems = []
    known = {e.evidence_id for e in incident.evidence}
    if unknown := [e for e in sub.evidence_ids if e not in known]:
        problems.append(f"evidence_ids not on this incident: {unknown}; valid: {sorted(known)}")
    if not sub.evidence_ids and sub.root_cause != FailureType.UNKNOWN:
        problems.append("evidence_ids: name at least one evidence item that supports the cause")
    if unseen := [c for c in sub.citations if c not in ctx.seen_chunks]:
        shown = sorted(ctx.seen_chunks) or "none yet: call search_runbooks"
        problems.append(f"citations must be chunk_ids returned by a tool in this run; not "
                        f"returned: {unseen}; returned so far: {shown}")  # fmt: skip
    if not sub.citations and sub.root_cause != FailureType.UNKNOWN:
        problems.append("citations: cite at least one runbook or past-incident chunk_id")
    if sub.action == ActionType.RESET_GPU and sub.action_gpu_index is None:
        problems.append("action_gpu_index is required for reset_gpu")
    if problems:
        return None, problems

    node_id = incident.components[0].node_id
    target = None
    if sub.action not in {ActionType.NONE, ActionType.MONITOR}:
        target = (
            ComponentRef(kind=ComponentKind.GPU, node_id=node_id, gpu_index=sub.action_gpu_index)
            if sub.action_gpu_index is not None and sub.action != ActionType.DRAIN_NODE
            else ComponentRef(kind=ComponentKind.NODE, node_id=node_id)
        )
    diagnosis = Diagnosis(
        root_cause=sub.root_cause,
        confidence=sub.confidence,
        summary=sub.summary,
        evidence_ids=list(dict.fromkeys(sub.evidence_ids)),
        citations=[
            Citation(chunk_id=c, doc_id=ctx.seen_chunks[c].doc_id)
            for c in dict.fromkeys(sub.citations)
        ],
        recommended_action=RecommendedAction(
            action=sub.action, target=target, rationale=sub.action_rationale
        ),
        model=model[:128],
        trace_id=run_id,
        created_at=datetime.now(UTC),
    )
    return diagnosis, []


ANSWER = "submit_answer"


class SubmitAnswer(Args):
    answer: str = Field(min_length=1, max_length=4000)
    citations: list[str] = Field(
        default_factory=list, max_length=10,
        description="chunk_ids returned by the tools that the answer relies on",
    )  # fmt: skip
    evidence_ids: list[str] = Field(
        default_factory=list, max_length=20,
        description="the incident's evidence items the answer refers to",
    )  # fmt: skip


class Answer(BaseModel):
    """A follow-up answer, held to the same rule as a diagnosis: only real citations."""

    answer: str
    citations: list[Citation]
    evidence_ids: list[str]
    model: str
    trace_id: uuid.UUID


ANSWER_SPEC = ToolSpec(
    ANSWER,
    "Submit the answer to the operator's question. Call it once, when you can answer.",
    json_schema(SubmitAnswer),
)


def check_answer(
    arguments: dict[str, object] | None,
    incident: Incident,
    ctx: ToolContext,
    model: str,
    run_id: uuid.UUID,
) -> tuple[Answer | None, list[str]]:
    if arguments is None:
        return None, ["arguments were not valid JSON"]
    try:
        sub = SubmitAnswer.model_validate(arguments)
    except ValidationError as exc:
        return None, [
            f"{'.'.join(map(str, e['loc'])) or 'arguments'}: {e['msg']}" for e in exc.errors()
        ]
    problems = []
    known = {e.evidence_id for e in incident.evidence}
    if unknown := [e for e in sub.evidence_ids if e not in known]:
        problems.append(f"evidence_ids not on this incident: {unknown}")
    if unseen := [c for c in sub.citations if c not in ctx.seen_chunks]:
        problems.append(f"citations must be chunk_ids a tool returned in this run: {unseen}")
    if problems:
        return None, problems
    return Answer(
        answer=sub.answer.strip(),
        citations=[Citation(chunk_id=c, doc_id=ctx.seen_chunks[c].doc_id)
                   for c in dict.fromkeys(sub.citations)],
        evidence_ids=list(dict.fromkeys(sub.evidence_ids)),
        model=model[:128],
        trace_id=run_id,
    ), []  # fmt: skip


_CLAIMED = re.compile(r"^\s*(citations|evidence_ids)\s*:.*$", re.IGNORECASE | re.MULTILINE)


def answer_from_text(text: str, model: str, run_id: uuid.UUID) -> Answer | None:
    """A chat reply written as plain text: keep the prose, drop any citations it claims (they
    weren't checked, so they aren't shown as sources)."""
    lines = text.strip().splitlines()
    if lines and lines[0].strip() == ANSWER:  # "submit_answer" written as a heading
        lines = lines[1:]
    prose = _CLAIMED.sub("", "\n".join(lines)).strip()
    if len(prose) < 2:
        return None
    return Answer(answer=prose[:4000], citations=[], evidence_ids=[], model=model[:128],
                  trace_id=run_id)  # fmt: skip
