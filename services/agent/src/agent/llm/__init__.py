"""Model providers. Choose one with AGENT_PROVIDER: openai_compat (default; Ollama or vLLM) or
anthropic. Setting AGENT_FALLBACK_PROVIDER adds a second provider that takes over when the first
fails or is slow (agent.llm.fallback); its settings use the same names with AGENT_FALLBACK_ in
place of AGENT_ (AGENT_FALLBACK_MODEL, AGENT_FALLBACK_BASE_URL, AGENT_FALLBACK_LLM_API_KEY)."""

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


def _price_from_env(prefix: str = "AGENT_") -> Price | None:
    if f"{prefix}PRICE_INPUT" not in os.environ and f"{prefix}PRICE_OUTPUT" not in os.environ:
        return None
    return Price(
        input=float(os.environ.get(f"{prefix}PRICE_INPUT", 0)),
        output=float(os.environ.get(f"{prefix}PRICE_OUTPUT", 0)),
    )


def _provider(kind: str, prefix: str) -> Provider:
    env = os.environ
    if kind == "openai_compat":
        from agent.llm.openai_compat import OpenAICompatProvider

        return OpenAICompatProvider(
            model=env.get(f"{prefix}MODEL", DEFAULT_LOCAL_MODEL),
            base_url=env.get(f"{prefix}BASE_URL", DEFAULT_BASE_URL),
            api_key=env.get(f"{prefix}LLM_API_KEY", "not-needed"),
            price=_price_from_env(prefix),
        )
    if kind == "anthropic":
        from agent.llm.claude import DEFAULT_MODEL, ClaudeProvider

        return ClaudeProvider(
            model=env.get(f"{prefix}MODEL", DEFAULT_MODEL), price=_price_from_env(prefix)
        )
    raise ValueError(f"unknown {prefix}PROVIDER {kind!r}; use openai_compat or anthropic")


def provider_from_env() -> Provider:
    primary = _provider(os.environ.get("AGENT_PROVIDER", "openai_compat"), "AGENT_")
    fallback = os.environ.get("AGENT_FALLBACK_PROVIDER")
    if not fallback:
        return primary
    from agent.llm.fallback import FallbackProvider

    return FallbackProvider(
        primary,
        _provider(fallback, "AGENT_FALLBACK_"),
        timeout_s=float(os.environ.get("AGENT_FALLBACK_AFTER_S", 120)),
        cooldown_s=float(os.environ.get("AGENT_FALLBACK_COOLDOWN_S", 60)),
    )
