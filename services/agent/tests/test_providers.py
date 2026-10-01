from types import SimpleNamespace
from typing import Any

import pytest
from anthropic.types.beta import BetaMessage
from openai.types.chat import ChatCompletion

from agent.llm import Price, ProviderError, ToolResult, ToolSpec, Usage
from agent.llm.claude import ClaudeProvider
from agent.llm.openai_compat import OpenAICompatProvider

SPEC = ToolSpec("get_logs", "Logs", {"type": "object", "properties": {}})


class FakeOpenAI:
    def __init__(self, *responses: Any) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return ChatCompletion.model_validate(r)


def completion(message: dict[str, Any], finish: str = "tool_calls") -> dict[str, Any]:
    return {
        "id": "c1", "object": "chat.completion", "created": 0, "model": "qwen3-agent",
        "choices": [{"index": 0, "finish_reason": finish,
                     "message": {"role": "assistant", **message}}],
        "usage": {"prompt_tokens": 900, "completion_tokens": 120, "total_tokens": 1020},
    }  # fmt: skip


def openai_provider(fake: FakeOpenAI) -> OpenAICompatProvider:
    return OpenAICompatProvider("qwen3-agent", "http://unused", client=fake)  # type: ignore[arg-type]


async def test_openai_compat_tool_calls_reasoning_and_transcript() -> None:
    fake = FakeOpenAI(completion({
        "content": "",
        "reasoning": "The fan is the likely cause.",
        "tool_calls": [
            {"id": "a", "type": "function",
             "function": {"name": "get_logs", "arguments": '{"node_id": "n1"}'}},
            {"id": "b", "type": "function", "function": {"name": "get_logs", "arguments": "{oops"}},
        ],
    }))  # fmt: skip
    session = openai_provider(fake).start("system", "investigate")
    turn = await session.next([SPEC])

    assert turn.stop_reason == "tool_use"
    assert [c.arguments for c in turn.tool_calls] == [{"node_id": "n1"}, None]
    assert turn.tool_calls[1].raw_arguments == "{oops"
    assert turn.reasoning == "The fan is the likely cause."
    assert turn.usage == Usage(900, 120)
    assert fake.requests[0]["tools"][0]["function"]["name"] == "get_logs"

    session.add_tool_results(
        [ToolResult("a", "get_logs", "[]"), ToolResult("b", "get_logs", "Error", True)]
    )
    msgs = session.messages  # type: ignore[attr-defined]
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "tool", "tool"]
    # The reasoning is not sent back; the tool calls are, with their original arguments.
    assert "reasoning" not in msgs[2]
    assert msgs[2]["tool_calls"][1]["function"]["arguments"] == "{oops"


async def test_openai_compat_strips_inline_thinking() -> None:
    fake = FakeOpenAI(completion({"content": "<think>hmm</think>Done."}, finish="stop"))
    turn = await openai_provider(fake).start("s", "u").next([SPEC])
    assert (turn.text, turn.reasoning, turn.stop_reason) == ("Done.", "<think>hmm</think>",
                                                              "end_turn")  # fmt: skip


async def test_openai_compat_errors_become_provider_errors() -> None:
    import httpx2
    import openai

    request = httpx2.Request("POST", "http://unused/v1/chat/completions")
    fake = FakeOpenAI(openai.APIConnectionError(request=request))
    with pytest.raises(ProviderError):
        await openai_provider(fake).start("s", "u").next([SPEC])


class FakeAnthropic:
    def __init__(self, *responses: dict[str, Any]) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs: Any) -> BetaMessage:
        self.requests.append(kwargs)
        return BetaMessage.model_validate(self.responses.pop(0))


def message(content: list[dict[str, Any]], stop: str = "tool_use") -> dict[str, Any]:
    return {
        "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
        "content": content, "stop_reason": stop, "stop_sequence": None,
        "usage": {"input_tokens": 50, "output_tokens": 200, "cache_read_input_tokens": 4000,
                  "cache_creation_input_tokens": 0},
    }  # fmt: skip


async def test_claude_session_keeps_content_and_batches_tool_results() -> None:
    fake = FakeAnthropic(message([
        {"type": "thinking", "thinking": "", "signature": "sig"},
        {"type": "tool_use", "id": "tu_1", "name": "get_logs", "input": {"node_id": "n1"}},
        {"type": "tool_use", "id": "tu_2", "name": "get_logs", "input": {"node_id": "n2"}},
    ]))  # fmt: skip
    provider = ClaudeProvider(client=fake)  # type: ignore[arg-type]
    session = provider.start("system prompt", "investigate")
    turn = await session.next([SPEC])

    assert [c.id for c in turn.tool_calls] == ["tu_1", "tu_2"]
    assert turn.usage == Usage(50, 200, cache_read_tokens=4000)
    request = fake.requests[0]
    assert request["model"] == "claude-opus-5-5"
    assert request["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert request["tools"][0]["input_schema"] == SPEC.parameters

    session.add_tool_results([ToolResult("tu_1", "get_logs", "[]"),
                              ToolResult("tu_2", "get_logs", "Error: x", True)])  # fmt: skip
    msgs = session.messages  # type: ignore[attr-defined]
    # The assistant content (thinking block included) goes back exactly as received...
    assert msgs[1]["content"][0].type == "thinking"
    # ...and all tool results of the turn go back in one user message.
    assert msgs[2]["role"] == "user" and len(msgs[2]["content"]) == 2
    assert msgs[2]["content"][1]["is_error"] is True


async def test_claude_refusal_is_reported() -> None:
    fake = FakeAnthropic(message([], stop="refusal"))
    turn = await ClaudeProvider(client=fake).start("s", "u").next([SPEC])  # type: ignore[arg-type]
    assert turn.stop_reason == "refusal" and turn.tool_calls == []


def test_prices() -> None:
    assert Price().cost(Usage(1_000_000, 1_000_000)) == 0  # self-hosted: no per-token bill
    opus = ClaudeProvider(client=object()).price  # type: ignore[arg-type]
    assert opus.cost(Usage(1_000_000, 100_000, cache_read_tokens=1_000_000)) == pytest.approx(
        4.0 + 2.0 + 0.2
    )


SUBMIT_SPEC = ToolSpec("submit_diagnosis", "Submit", {"type": "object", "properties": {}})
CALL_TEXT = '{"name": "submit_diagnosis", "arguments": {"root_cause": "gpu_off_bus"}}'


@pytest.mark.parametrize(
    "content",
    [
        CALL_TEXT,
        f"<tool_call>\n{CALL_TEXT}\n</tool_call>",
        f"```json\n{CALL_TEXT}\n```",
        f"{CALL_TEXT}\n</tool_call>\n",  # seen live: the server ate the opening tag
        f"<tool_call>{CALL_TEXT}",
    ],
)
async def test_openai_compat_recovers_tool_calls_written_as_text(content: str) -> None:
    fake = FakeOpenAI(completion({"content": content}, finish="stop"))
    session = openai_provider(fake).start("s", "u")
    turn = await session.next([SPEC, SUBMIT_SPEC])
    assert turn.stop_reason == "tool_use" and turn.text == ""
    assert [(c.name, c.arguments) for c in turn.tool_calls] == [
        ("submit_diagnosis", {"root_cause": "gpu_off_bus"})
    ]
    assert turn.tool_calls[0].id.startswith("text_")
    # The transcript records a real tool call, so the tool result that follows pairs with it.
    assistant = session.messages[-1]  # type: ignore[attr-defined]
    assert assistant["content"] == ""
    assert assistant["tool_calls"][0]["id"] == turn.tool_calls[0].id


@pytest.mark.parametrize(
    "content",
    [
        CALL_TEXT.replace("submit_diagnosis", "format_disk"),  # not an offered tool
        f"I'll submit now: {CALL_TEXT}",  # prose around it: leave it as text
        '{"name": "submit_diagnosis"}',  # no arguments
        "The root cause is a fan failure.",
    ],
)
async def test_openai_compat_leaves_other_text_alone(content: str) -> None:
    fake = FakeOpenAI(completion({"content": content}, finish="stop"))
    turn = await openai_provider(fake).start("s", "u").next([SPEC, SUBMIT_SPEC])
    assert turn.tool_calls == [] and turn.stop_reason == "end_turn" and turn.text == content


async def test_openai_compat_never_sends_null_content() -> None:
    # The model spent max_tokens on reasoning: no text, no tool calls.
    fake = FakeOpenAI(completion({"content": None, "reasoning": "long..."}, finish="length"))
    session = openai_provider(fake).start("s", "u")
    turn = await session.next([SPEC])
    assert turn.stop_reason == "max_tokens" and turn.tool_calls == []
    assert session.messages[-1] == {"role": "assistant", "content": ""}  # type: ignore[attr-defined]
