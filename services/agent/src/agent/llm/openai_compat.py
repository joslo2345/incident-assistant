"""Any server that speaks the OpenAI chat-completions API with tool calling: Ollama on a laptop
today, vLLM on a GPU node in B5. Self-hosted, so the default price is zero.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any

import openai

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

# Reasoning models served without a separate reasoning channel put it inline; never treat it as
# the answer or send it back.
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)
# Where a tool call written as text can sit: Hermes-style tags, a ```json fence, or the bare text.
_TAGGED = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.DOTALL)
_STRAY_TAG = re.compile(r"^\s*(?:<tool_call>)?\s*(.*?)\s*(?:</tool_call>)?\s*$", re.DOTALL)
# `submit_answer` on its own line, then the arguments object (seen in A7 follow-ups).
_NAMED = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*\n\s*(\{.*\})$", re.DOTALL)
_FENCED = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


def text_tool_calls(text: str, offered: set[str]) -> list[tuple[str, dict[str, Any]]]:
    """Tool calls a local model wrote into its message text instead of the tool-call channel.

    Qwen3 under Ollama sometimes answers with `{"name": ..., "arguments": {...}}` as plain text,
    which the server's parser misses. Only a message that is nothing but such calls, naming tools
    offered on this turn, counts; anything else stays text.
    """
    blocks = _TAGGED.findall(text)
    if not blocks:
        # The server may consume one tag of the pair, leaving e.g. `{...}\n</tool_call>`.
        text = _STRAY_TAG.match(text).group(1)  # type: ignore[union-attr]
        fenced = _FENCED.match(text)
        text = fenced.group(1) if fenced else text
        named = _NAMED.match(text)
        if named and named.group(1) in offered:
            try:
                args = json.loads(named.group(2))
            except json.JSONDecodeError:
                return []
            return [(named.group(1), args)] if isinstance(args, dict) else []
        blocks = [text]
    calls = []
    for block in blocks:
        try:
            obj = json.loads(block)
        except json.JSONDecodeError:
            return []
        args = obj.get("arguments", obj.get("parameters")) if isinstance(obj, dict) else None
        if not isinstance(args, dict) or obj.get("name") not in offered:
            return []
        calls.append((str(obj["name"]), args))
    return calls


class OpenAICompatProvider:
    name = "openai_compat"

    def __init__(
        self,
        model: str,
        base_url: str,
        api_key: str = "not-needed",
        price: Price | None = None,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        timeout_s: float = 300.0,
        client: openai.AsyncOpenAI | None = None,
    ) -> None:
        self.model = model
        self.price = price or Price()
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.client = client or openai.AsyncOpenAI(
            base_url=base_url, api_key=api_key, timeout=timeout_s, max_retries=1
        )

    def start(self, system: str, user: str) -> ChatSession:
        return OpenAICompatSession(
            self, [{"role": "system", "content": system}, {"role": "user", "content": user}]
        )


class OpenAICompatSession:
    def __init__(self, provider: OpenAICompatProvider, messages: list[dict[str, Any]]) -> None:
        self.p = provider
        self.messages = messages

    async def next(self, tools: Sequence[ToolSpec]) -> Turn:
        try:
            response = await self.p.client.chat.completions.create(
                model=self.p.model,
                messages=self.messages,  # type: ignore[arg-type]
                tools=[
                    {
                        "type": "function",
                        "function": {
                            "name": t.name,
                            "description": t.description,
                            "parameters": t.parameters,
                        },
                    }
                    for t in tools
                ],
                temperature=self.p.temperature,
                max_tokens=self.p.max_tokens,
            )
        except (openai.APIConnectionError, openai.APIStatusError) as exc:
            raise ProviderError(f"{type(exc).__name__}: {exc}") from exc
        if not response.choices:
            raise ProviderError("response had no choices")
        choice = response.choices[0]
        msg = choice.message
        raw_text = msg.content or ""
        text = _THINK.sub("", raw_text).strip()
        # Ollama and vLLM return a reasoning model's thinking in a non-standard field.
        extra = msg.model_extra or {}
        reasoning = str(extra.get("reasoning") or extra.get("reasoning_content") or "")
        reasoning = reasoning or "".join(m.group(0) for m in _THINK.finditer(raw_text))
        calls = []
        for i, tc in enumerate(msg.tool_calls or []):
            fn = getattr(tc, "function", None)
            if fn is None:
                continue
            try:
                args = json.loads(fn.arguments or "{}")
            except json.JSONDecodeError:
                args = None
            calls.append(
                ToolCall(
                    id=tc.id or f"call_{len(self.messages)}_{i}",
                    name=fn.name,
                    arguments=args if isinstance(args, dict) else None,
                    raw_arguments=fn.arguments or "",
                )
            )
        if not calls and text:
            recovered = text_tool_calls(text, {t.name for t in tools})
            calls = [
                ToolCall(f"text_{len(self.messages)}_{i}", name, args, json.dumps(args))
                for i, (name, args) in enumerate(recovered)
            ]
            if calls:
                # Record it as the tool call it was, so the tool results that follow pair up.
                text = ""
        self.messages.append(
            {
                "role": "assistant",
                # Never null: a turn that used its whole budget on reasoning has no text and no
                # tool calls, and Ollama rejects a null-content assistant message on the next
                # request (found by the A6 baseline).
                "content": text,
                **(
                    {
                        "tool_calls": [
                            {
                                "id": c.id,
                                "type": "function",
                                "function": {"name": c.name, "arguments": c.raw_arguments},
                            }
                            for c in calls
                        ]
                    }
                    if calls
                    else {}
                ),
            }
        )
        stop: StopReason = (
            "tool_use"
            if calls
            else "max_tokens"
            if choice.finish_reason == "length"
            else "end_turn"
        )
        u = response.usage
        usage = Usage(
            input_tokens=u.prompt_tokens if u else 0,
            output_tokens=u.completion_tokens if u else 0,
        )
        return Turn(text, calls, usage, stop, response.model or self.p.model, reasoning)

    def add_tool_results(self, results: Sequence[ToolResult]) -> None:
        for r in results:
            self.messages.append({"role": "tool", "tool_call_id": r.call_id, "content": r.content})

    def add_user(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})
