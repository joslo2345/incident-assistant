"""Model providers. Choose one with AGENT_PROVIDER: openai_compat (default; Ollama or vLLM) or
anthropic."""

from __future__ import annotations

import os

from agent.llm.base import (
    ChatSession,
    Price,
    Provider,
    ProviderError,
    ScriptedProvider,
    ToolCall,
    ToolResult,
    ToolSpec,
    Turn,
    Usage,
)

__all__ = [
    "ChatSession",
    "Price",
    "Provider",
    "ProviderError",
    "ScriptedProvider",
    "ToolCall",
    "ToolResult",
    "ToolSpec",
    "Turn",
    "Usage",
    "provider_from_env",
]

DEFAULT_LOCAL_MODEL = "qwen3-agent"  # qwen3:30b-a3b with a 32k context; see deploy/ollama
DEFAULT_BASE_URL = "http://127.0.0.1:11434/v1"


def _price_from_env() -> Price | None:
    if "AGENT_PRICE_INPUT" not in os.environ and "AGENT_PRICE_OUTPUT" not in os.environ:
        return None
    return Price(
        input=float(os.environ.get("AGENT_PRICE_INPUT", 0)),
        output=float(os.environ.get("AGENT_PRICE_OUTPUT", 0)),
    )


def provider_from_env() -> Provider:
    kind = os.environ.get("AGENT_PROVIDER", "openai_compat")
    if kind == "openai_compat":
        from agent.llm.openai_compat import OpenAICompatProvider

        return OpenAICompatProvider(
            model=os.environ.get("AGENT_MODEL", DEFAULT_LOCAL_MODEL),
            base_url=os.environ.get("AGENT_BASE_URL", DEFAULT_BASE_URL),
            api_key=os.environ.get("AGENT_LLM_API_KEY", "not-needed"),
            price=_price_from_env(),
        )
    if kind == "anthropic":
        from agent.llm.claude import DEFAULT_MODEL, ClaudeProvider

        return ClaudeProvider(
            model=os.environ.get("AGENT_MODEL", DEFAULT_MODEL), price=_price_from_env()
        )
    raise ValueError(f"unknown AGENT_PROVIDER {kind!r}; use openai_compat or anthropic")
