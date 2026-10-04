"""Claude through the Anthropic API. Optional: used only when AGENT_PROVIDER=anthropic and a
credential is configured. The assistant's content is appended back unchanged every turn, so
thinking blocks stay valid across tool calls.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any, Literal

import anthropic
from anthropic.types.beta import BetaMessageParam, BetaTextBlockParam, BetaToolParam

from agent.llm.base import (
    ChatSession,
    Price,
    ProviderError,
    StopReason,
    ToolCall,
    ToolResult,
    ToolSpec,
    Turn,
    Usage,
)

DEFAULT_MODEL = "claude-opus-5-5"
# USD per million tokens (input, output, cache read, cache write at 1.25x input).
PRICES = {"claude-opus-5-5": Price(4.0, 20.0, 0.20, 5.0)}
Effort = Literal["low", "medium", "high", "xhigh", "max"]
_ID_CHARS = re.compile(r"[^a-zA-Z0-9_-]")


def _tool_id(call_id: str) -> str:
    """Claude accepts tool-use ids of letters, digits, _ and - only; ids from another provider
    (a fallback taking over a conversation) are mapped the same way in calls and results."""
    return _ID_CHARS.sub("_", call_id)


class ClaudeProvider:
    name = "anthropic"

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        effort: Effort = "medium",
        price: Price | None = None,
        timeout_s: float = 120.0,
        client: anthropic.AsyncAnthropic | None = None,
    ) -> None:
        self.model = model
        self.effort = effort
        self.price = price or PRICES.get(model, Price())
        self.client = client or anthropic.AsyncAnthropic(timeout=timeout_s, max_retries=2)

    def start(self, system: str, user: str) -> ChatSession:
        return ClaudeSession(self, system, [{"role": "user", "content": user}])


class ClaudeSession:
    def __init__(
        self, provider: ClaudeProvider, system: str, messages: list[BetaMessageParam]
    ) -> None:
        self.p = provider
        # The system prompt and tool list are identical on every call; cache that prefix.
        self.system: list[BetaTextBlockParam] = [
            {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
        ]
        self.messages: list[BetaMessageParam] = messages

    async def next(self, tools: Sequence[ToolSpec]) -> Turn:
        tool_params: list[BetaToolParam] = [
            {"name": t.name, "description": t.description, "input_schema": t.parameters}
            for t in tools
        ]
        try:
            response = await self.p.client.beta.messages.create(
                model=self.p.model,
                max_tokens=16000,
                # On a safety-classifier false positive, retry server-side on the recommended
                # fallback model instead of failing the investigation.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                output_config={"effort": self.p.effort},
                system=self.system,
                tools=tool_params,
                messages=self.messages,
            )
        except (anthropic.APIConnectionError, anthropic.RateLimitError) as exc:
            raise ProviderError(f"{type(exc).__name__}: {exc}") from exc
        except anthropic.APIStatusError as exc:
            if exc.status_code < 500:
                raise  # a 4xx other than 429 is a bug in our request; don't hide it
            raise ProviderError(f"server error {exc.status_code}") from exc
        self.messages.append({"role": "assistant", "content": response.content})
        calls = [
            ToolCall(b.id, b.name, b.input if isinstance(b.input, dict) else None,
                     json.dumps(b.input))
            for b in response.content
            if b.type == "tool_use"
        ]  # fmt: skip
        text = "".join(b.text for b in response.content if b.type == "text").strip()
        stop: StopReason = {
            "tool_use": "tool_use",
            "max_tokens": "max_tokens",
            "refusal": "refusal",
        }.get(response.stop_reason or "", "end_turn")  # type: ignore[assignment]
        u = response.usage
        usage = Usage(
            input_tokens=u.input_tokens,
            output_tokens=u.output_tokens,
            cache_read_tokens=u.cache_read_input_tokens or 0,
            cache_write_tokens=u.cache_creation_input_tokens or 0,
        )
        return Turn(text, calls, usage, stop, response.model)

    def add_tool_results(self, results: Sequence[ToolResult]) -> None:
        # All results of one turn go back in a single user message.
        self.messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": _tool_id(r.call_id),
                        "content": r.content,
                        "is_error": r.is_error,
                    }
                    for r in results
                ],
            }
        )

    def add_user(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    def add_assistant(self, turn: Turn) -> None:
        # A turn from another model: its text and tool calls, without thinking blocks.
        content: list[Any] = [{"type": "text", "text": turn.text}] if turn.text else []
        content += [
            {"type": "tool_use", "id": _tool_id(c.id), "name": c.name, "input": c.arguments or {}}
            for c in turn.tool_calls
        ]
        self.messages.append(
            {"role": "assistant", "content": content or [{"type": "text", "text": "(no output)"}]}
        )
