"""Provider-neutral chat interface for the agent loop.

The loop only sees `Provider.start()` -> `ChatSession.next()` -> `Turn`. Each session keeps the
provider's own native transcript and only ever appends to it, so provider-specific content
(Claude's thinking blocks, OpenAI tool-call ids) goes back to the model exactly as it came out.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

StopReason = Literal["tool_use", "end_turn", "max_tokens", "refusal"]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema of the arguments object


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] | None  # None when the model sent arguments that aren't JSON
    raw_arguments: str = ""


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    name: str
    content: str
    is_error: bool = False


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @property
    def total(self) -> int:
        return (
            self.input_tokens + self.output_tokens + self.cache_read_tokens
            + self.cache_write_tokens
        )  # fmt: skip


@dataclass(frozen=True)
class Turn:
    text: str
    tool_calls: list[ToolCall]
    usage: Usage
    stop_reason: StopReason
    model: str
    reasoning: str = ""  # kept for the trace only; never sent back to the model


@dataclass(frozen=True)
class Price:
    """USD per million tokens. A self-hosted model is all zeros: no per-token bill."""

    input: float = 0.0
    output: float = 0.0
    cache_read: float = 0.0
    cache_write: float = 0.0

    def cost(self, u: Usage) -> float:
        return (
            u.input_tokens * self.input + u.output_tokens * self.output
            + u.cache_read_tokens * self.cache_read + u.cache_write_tokens * self.cache_write
        ) / 1_000_000  # fmt: skip


class ChatSession(Protocol):
    async def next(self, tools: Sequence[ToolSpec]) -> Turn: ...
    def add_tool_results(self, results: Sequence[ToolResult]) -> None: ...
    def add_user(self, text: str) -> None: ...


class Provider(Protocol):
    name: str
    model: str
    price: Price

    def start(self, system: str, user: str) -> ChatSession: ...


class ProviderError(Exception):
    """The model could not produce a turn (connection, server error, malformed response)."""


@dataclass
class ScriptedProvider:
    """Replays a fixed list of turns. For tests and CI: no model, fully deterministic.

    Each entry is either a Turn or a callable taking the session (to inspect what the agent sent
    so far) and returning a Turn.
    """

    turns: list[Any]
    name: str = "scripted"
    model: str = "scripted"
    price: Price = field(default_factory=Price)
    sessions: list[ScriptedSession] = field(default_factory=list)

    def start(self, system: str, user: str) -> ChatSession:
        session = ScriptedSession(self, system, [("user", user)])
        self.sessions.append(session)
        return session


@dataclass
class ScriptedSession:
    provider: ScriptedProvider
    system: str
    transcript: list[tuple[str, Any]]
    offered_tools: list[list[str]] = field(default_factory=list)

    async def next(self, tools: Sequence[ToolSpec]) -> Turn:
        self.offered_tools.append([t.name for t in tools])
        if not self.provider.turns:
            raise ProviderError("script exhausted")
        entry = self.provider.turns.pop(0)
        turn: Turn = entry(self) if callable(entry) else entry
        self.transcript.append(("assistant", turn))
        return turn

    def add_tool_results(self, results: Sequence[ToolResult]) -> None:
        self.transcript.append(("tool", list(results)))

    def add_user(self, text: str) -> None:
        self.transcript.append(("user", text))
