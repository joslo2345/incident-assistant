"""Record a model's replies during an eval run and replay them later, without the model.

CI can't run the local model, so the CI eval replays a recording: the agent loop, tools,
validation and scoring all run for real against a freshly replayed stack; only the model's
turns come from the file. A change that breaks a tool, the loop or the checks shows up as a
failed run, more tool errors, or lower scores than the recording had.

The CI data is replayed relative to "now", so timestamps and incident IDs in tool arguments
differ between recording and replay. Recorded turns store timestamps as offsets from the
incident's first signal (`{{t+600}}`) and the incident ID as `{{incident_id}}`.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from agent.llm import (
    ChatSession,
    Price,
    Provider,
    ProviderError,
    ToolCall,
    ToolResult,
    ToolSpec,
    Turn,
    Usage,
)
from incident_contracts import Incident

_ISO = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})?")
_OFFSET = re.compile(r"\{\{t([+-]\d+)\}\}")


def anchor(incident: Incident) -> datetime:
    return min(e.window_start for e in incident.evidence)


def _template(text: str, incident: Incident) -> str:
    base = anchor(incident)

    def offset(m: re.Match[str]) -> str:
        t = datetime.fromisoformat(m.group(0).replace("Z", "+00:00"))
        t = t if t.tzinfo else t.replace(tzinfo=UTC)
        return f"{{{{t{int((t - base).total_seconds()):+d}}}}}"

    return _ISO.sub(offset, text).replace(str(incident.incident_id), "{{incident_id}}")


def _render(text: str, incident: Incident) -> str:
    base = anchor(incident)

    def iso(m: re.Match[str]) -> str:
        t = base + timedelta(seconds=int(m.group(1)))
        return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    return _OFFSET.sub(iso, text).replace("{{incident_id}}", str(incident.incident_id))


def turn_to_json(turn: Turn, incident: Incident) -> dict[str, Any]:
    raw = {
        "text": turn.text,
        "tool_calls": [asdict(c) for c in turn.tool_calls],
        "usage": asdict(turn.usage),
        "stop_reason": turn.stop_reason,
        "model": turn.model,
    }
    return json.loads(_template(json.dumps(raw), incident))  # type: ignore[no-any-return]


def turn_from_json(data: dict[str, Any], incident: Incident) -> Turn:
    d = json.loads(_render(json.dumps(data), incident))
    return Turn(
        text=d["text"],
        tool_calls=[ToolCall(**c) for c in d["tool_calls"]],
        usage=Usage(**d["usage"]),
        stop_reason=d["stop_reason"],
        model=d["model"],
    )


@dataclass
class Cassette:
    cases: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    recorded_with: dict[str, Any] = field(default_factory=dict)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=1) + "\n")

    @classmethod
    def load(cls, path: Path) -> Cassette:
        if not path.exists():
            raise SystemExit(f"no recording at {path}; record one with `make eval-ci-record`")
        return cls(**json.loads(path.read_text()))


class RecordingProvider:
    """Passes calls to the real provider and keeps each turn under the current case."""

    def __init__(self, inner: Provider, cassette: Cassette) -> None:
        self.inner = inner
        self.cassette = cassette
        self.name, self.model, self.price = inner.name, inner.model, inner.price
        self.case_id = ""
        self.incident: Incident | None = None

    def begin(self, case_id: str, incident: Incident) -> None:
        self.case_id, self.incident = case_id, incident
        self.cassette.cases[case_id] = []

    def start(self, system: str, user: str) -> ChatSession:
        return _RecordingSession(self, self.inner.start(system, user))


class _RecordingSession:
    def __init__(self, p: RecordingProvider, inner: ChatSession) -> None:
        self.p, self.inner = p, inner

    async def next(self, tools: Sequence[ToolSpec]) -> Turn:
        turn = await self.inner.next(tools)
        assert self.p.incident is not None
        self.p.cassette.cases[self.p.case_id].append(turn_to_json(turn, self.p.incident))
        return turn

    def add_tool_results(self, results: Sequence[ToolResult]) -> None:
        self.inner.add_tool_results(results)

    def add_user(self, text: str) -> None:
        self.inner.add_user(text)


class ReplayProvider:
    """Serves recorded turns in order; runs out (and fails the run) if the loop asks for more."""

    def __init__(self, cassette: Cassette) -> None:
        self.cassette = cassette
        recorded = cassette.recorded_with
        self.name = "replay"
        self.model = f"replay:{recorded.get('model', '?')}"
        self.price = Price()
        self.turns: list[dict[str, Any]] = []
        self.incident: Incident | None = None

    def begin(self, case_id: str, incident: Incident) -> None:
        if case_id not in self.cassette.cases:
            raise ProviderError(f"no recording for case {case_id}")
        self.turns, self.incident = list(self.cassette.cases[case_id]), incident

    def start(self, system: str, user: str) -> ChatSession:
        return _ReplaySession(self)


class _ReplaySession:
    def __init__(self, p: ReplayProvider) -> None:
        self.p = p

    async def next(self, tools: Sequence[ToolSpec]) -> Turn:
        if not self.p.turns:
            raise ProviderError("recording exhausted: the loop asked for more turns than recorded")
        assert self.p.incident is not None
        return turn_from_json(self.p.turns.pop(0), self.p.incident)

    def add_tool_results(self, results: Sequence[ToolResult]) -> None:
        pass

    def add_user(self, text: str) -> None:
        pass
