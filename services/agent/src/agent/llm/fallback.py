"""Fallback between two providers: the primary (the self-hosted model) answers while it's healthy;
when a call fails or takes too long, the conversation moves to the secondary (the hosted API) and
stays there. Enabled with AGENT_FALLBACK_PROVIDER (see provider_from_env).

Moving a conversation replays it into a fresh secondary session: the same system prompt and
incident, then every assistant turn, tool result and user message so far, in the secondary's own
format. The investigation continues where it stopped instead of starting over (tool calls already
made aren't repeated).

After a failure the primary is skipped for `cooldown_s`, so the incidents that follow go straight
to the secondary instead of each waiting out the timeout.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Sequence
from typing import Any

from agent.llm.base import (
    ChatSession,
    Price,
    Provider,
    ProviderError,
    ToolResult,
    ToolSpec,
    Turn,
)

log = logging.getLogger(__name__)


class FallbackProvider:
    def __init__(
        self,
        primary: Provider,
        secondary: Provider,
        timeout_s: float = 120.0,
        cooldown_s: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.primary = primary
        self.secondary = secondary
        self.timeout_s = timeout_s
        self.cooldown_s = cooldown_s
        self.clock = clock
        self.name = f"{primary.name}+fallback:{secondary.name}"
        self.model = primary.model
        self.price = primary.price
        self.down_until = 0.0
        self.failovers = 0

    def primary_up(self) -> bool:
        return self.clock() >= self.down_until

    def mark_primary_down(self, reason: str) -> None:
        self.down_until = self.clock() + self.cooldown_s
        self.failovers += 1
        log.warning("primary model %s failed (%s); using %s", self.primary.model, reason,
                    self.secondary.model)  # fmt: skip

    def turn_cost(self, turn: Turn) -> float:
        """Cost at the price of whichever provider produced the turn."""
        price: Price = (
            self.secondary.price if turn.model == self.secondary.model else self.primary.price
        )
        return price.cost(turn.usage)

    def start(self, system: str, user: str) -> ChatSession:
        return FallbackSession(self, system, user)


class FallbackSession:
    def __init__(self, provider: FallbackProvider, system: str, user: str) -> None:
        self.p = provider
        self.system = system
        self.user = user
        self.history: list[tuple[str, Any]] = []  # provider-neutral, for replay
        self.on_secondary = not provider.primary_up()
        self.session = (provider.secondary if self.on_secondary else provider.primary).start(
            system, user
        )

    def _move_to_secondary(self) -> None:
        session = self.p.secondary.start(self.system, self.user)
        for kind, item in self.history:
            if kind == "assistant":
                session.add_assistant(item)
            elif kind == "tool":
                session.add_tool_results(item)
            else:
                session.add_user(item)
        self.session = session
        self.on_secondary = True

    async def next(self, tools: Sequence[ToolSpec]) -> Turn:
        if not self.on_secondary:
            try:
                turn = await asyncio.wait_for(self.session.next(tools), self.p.timeout_s)
            except (ProviderError, TimeoutError) as exc:
                self.p.mark_primary_down(f"{type(exc).__name__}: {exc}"[:200])
                self._move_to_secondary()
            else:
                self.history.append(("assistant", turn))
                return turn
        turn = await self.session.next(tools)
        self.history.append(("assistant", turn))
        return turn

    def add_tool_results(self, results: Sequence[ToolResult]) -> None:
        self.history.append(("tool", list(results)))
        self.session.add_tool_results(results)

    def add_user(self, text: str) -> None:
        self.history.append(("user", text))
        self.session.add_user(text)

    def add_assistant(self, turn: Turn) -> None:
        self.history.append(("assistant", turn))
        self.session.add_assistant(turn)
