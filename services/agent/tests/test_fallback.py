"""Fallback from the self-hosted model to the hosted API (B5): when it switches, what the
secondary sees, cooldown, cost, and configuration."""

import asyncio
from typing import Any

import pytest

from agent.llm import (
    Price,
    ProviderError,
    ScriptedProvider,
    ToolCall,
    ToolResult,
    ToolSpec,
    Turn,
    Usage,
    provider_from_env,
)
from agent.llm.claude import ClaudeProvider
from agent.llm.fallback import FallbackProvider, FallbackSession
from agent.llm.openai_compat import OpenAICompatProvider

SPEC = ToolSpec("get_logs", "Logs", {"type": "object", "properties": {}})


def turn(model: str, text: str = "", call_id: str = "") -> Turn:
    calls = (
        [ToolCall(call_id, "get_logs", {"node_id": "n1"}, '{"node_id": "n1"}')] if call_id else []
    )
    return Turn(text, calls, Usage(1000, 100), "tool_use" if calls else "end_turn", model)


def down(_: Any) -> Turn:
    raise ProviderError("APIConnectionError: connection refused")


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def providers(primary_turns: list[Any], secondary_turns: list[Any]) -> tuple[Any, Any]:
    primary = ScriptedProvider(primary_turns, name="openai_compat", model="qwen3.5-9b",
                               price=Price())  # fmt: skip
    secondary = ScriptedProvider(secondary_turns, name="anthropic", model="claude-opus-5-5",
                                 price=Price(4.0, 20.0))  # fmt: skip
    return primary, secondary


async def test_healthy_primary_answers_alone() -> None:
    primary, secondary = providers(
        [turn("qwen3.5-9b", call_id="a"), turn("qwen3.5-9b", "done")], []
    )
    session = FallbackProvider(primary, secondary).start("system", "incident")
    assert (await session.next([SPEC])).model == "qwen3.5-9b"
    session.add_tool_results([ToolResult("a", "get_logs", "{}")])
    assert (await session.next([SPEC])).text == "done"
    assert secondary.sessions == []


async def test_failure_moves_the_conversation_to_the_secondary() -> None:
    first = turn("qwen3.5-9b", call_id="chatcmpl-tool-1")
    primary, secondary = providers([first, down], [turn("claude-opus-5-5", "diagnosed")])
    fallback = FallbackProvider(primary, secondary)
    session = fallback.start("system", "incident")
    await session.next([SPEC])
    results = [ToolResult("chatcmpl-tool-1", "get_logs", '{"lines": []}')]
    session.add_tool_results(results)
    session.add_user("Continue the investigation.")

    second = await session.next([SPEC])

    assert second.model == "claude-opus-5-5"
    assert fallback.failovers == 1
    # The secondary got the whole conversation, in order, and continued from there.
    replay = secondary.sessions[0]
    assert replay.system == "system"
    assert replay.transcript[:4] == [
        ("user", "incident"), ("assistant", first), ("tool", results),
        ("user", "Continue the investigation."),
    ]  # fmt: skip
    assert isinstance(session, FallbackSession) and session.on_secondary


async def test_slow_primary_times_out_into_the_secondary() -> None:
    class Slow:
        name, model, price = "openai_compat", "qwen3.5-9b", Price()

        def start(self, system: str, user: str) -> Any:
            class S:
                async def next(self, tools: Any) -> Turn:
                    await asyncio.sleep(5)
                    raise AssertionError("not reached")

            return S()

    _, secondary = providers([], [turn("claude-opus-5-5", "ok")])
    session = FallbackProvider(Slow(), secondary, timeout_s=0.01).start("s", "u")
    assert (await session.next([SPEC])).text == "ok"


async def test_after_a_failure_new_incidents_skip_the_primary_until_the_cooldown_ends() -> None:
    clock = Clock()
    primary, secondary = providers(
        [down, turn("qwen3.5-9b", "back")],
        [turn("claude-opus-5-5", "a"), turn("claude-opus-5-5", "b")],
    )
    fallback = FallbackProvider(primary, secondary, cooldown_s=60, clock=clock)
    assert (await fallback.start("s", "incident 1").next([SPEC])).text == "a"
    clock.now = 30
    assert (await fallback.start("s", "incident 2").next([SPEC])).text == "b"
    assert len(primary.sessions) == 1  # incident 2 never tried the primary
    clock.now = 61
    assert (await fallback.start("s", "incident 3").next([SPEC])).text == "back"


def test_each_turn_costs_what_its_model_costs() -> None:
    primary, secondary = providers([], [])
    fallback = FallbackProvider(primary, secondary)
    assert fallback.turn_cost(turn("qwen3.5-9b")) == 0
    assert fallback.turn_cost(turn("claude-opus-5-5")) == pytest.approx((1000 * 4 + 100 * 20) / 1e6)


def test_configured_through_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_PROVIDER", "openai_compat")
    monkeypatch.setenv("AGENT_BASE_URL", "http://vllm.selfhost.svc:8000/v1")
    monkeypatch.setenv("AGENT_MODEL", "qwen3.5-9b")
    monkeypatch.setenv("AGENT_FALLBACK_PROVIDER", "openai_compat")
    monkeypatch.setenv("AGENT_FALLBACK_BASE_URL", "http://ollama:11434/v1")
    monkeypatch.setenv("AGENT_FALLBACK_MODEL", "qwen3-agent")
    monkeypatch.setenv("AGENT_FALLBACK_AFTER_S", "30")
    p = provider_from_env()
    assert isinstance(p, FallbackProvider)
    assert (p.primary.model, p.secondary.model, p.timeout_s) == ("qwen3.5-9b", "qwen3-agent", 30)
    monkeypatch.delenv("AGENT_FALLBACK_PROVIDER")
    assert isinstance(provider_from_env(), OpenAICompatProvider)


def test_replayed_turns_use_each_providers_format() -> None:
    qwen_turn = turn("qwen3.5-9b", "Checking logs.", call_id="chatcmpl-tool.86c8")
    results = [ToolResult("chatcmpl-tool.86c8", "get_logs", "{}")]

    claude = ClaudeProvider(client=object()).start("s", "u")  # type: ignore[arg-type]
    claude.add_assistant(qwen_turn)
    claude.add_tool_results(results)
    msgs: list[Any] = claude.messages  # type: ignore[attr-defined]
    tool_use = msgs[1]["content"][1]
    assert tool_use["id"] == "chatcmpl-tool_86c8"  # Claude's id alphabet
    assert msgs[2]["content"][0]["tool_use_id"] == tool_use["id"]  # results still pair up

    openai = OpenAICompatProvider("m", "http://unused", client=object()).start("s", "u")  # type: ignore[arg-type]
    openai.add_assistant(turn("claude-opus-5-5", "", call_id="toolu_1"))
    sent: list[Any] = openai.messages  # type: ignore[attr-defined]
    assert sent[2]["content"] == ""  # never null
    assert sent[2]["tool_calls"][0]["function"]["arguments"] == '{"node_id": "n1"}'
