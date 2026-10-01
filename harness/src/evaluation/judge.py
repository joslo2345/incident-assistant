"""LLM judge for the one metric code can't score: does a cited passage support the diagnosis?

The agent already can't cite a chunk it wasn't shown (checked in the loop). This asks the next
question: is that chunk actually evidence for what the diagnosis says, or a citation added for
show? The judge is a different model family from the agent (Gemma 3 vs Qwen3), so it isn't
grading its own style, and it is calibrated against hand grades (`evaluate calibrate`).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import openai

DEFAULT_JUDGE = "gemma3:12b"

JUDGE_PROMPT = """\
You check citations in incident diagnoses written for GPU datacenter operators.

You get a diagnosis (root cause, summary, recommended action and its rationale) and ONE passage \
that the diagnosis cites. Decide whether the passage supports the diagnosis.

The passage SUPPORTS the diagnosis if it states facts that back the stated root cause given the \
observations in the summary (for example, it describes those symptoms for that failure type), or \
it backs the recommended action for that root cause (for example, a remediation step).

The passage does NOT support the diagnosis if it is about a different failure type or action, \
only shares keywords, is generic, or contradicts the diagnosis.

Judge only the passage against the diagnosis. Do not judge whether the diagnosis itself is right.

Reply with JSON: {"supported": true or false, "reason": "<one sentence>"}"""

VERDICT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"supported": {"type": "boolean"}, "reason": {"type": "string"}},
    "required": ["supported", "reason"],
}


@dataclass(frozen=True)
class Verdict:
    chunk_id: str
    supported: bool
    reason: str


def diagnosis_text(root_cause: str, summary: str, action: str, rationale: str) -> str:
    return (
        f"Root cause: {root_cause}\nSummary: {summary}\n"
        f"Recommended action: {action}\nRationale: {rationale}"
    )


class Judge:
    def __init__(
        self,
        model: str = DEFAULT_JUDGE,
        base_url: str = "http://127.0.0.1:11434/v1",
        client: openai.AsyncOpenAI | None = None,
    ) -> None:
        self.model = model
        self.client = client or openai.AsyncOpenAI(
            base_url=base_url, api_key="not-needed", timeout=180, max_retries=1
        )

    async def judge(self, diagnosis: str, chunk_id: str, passage: str) -> Verdict:
        response = await self.client.chat.completions.create(
            model=self.model,
            temperature=0,
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "verdict", "schema": VERDICT_SCHEMA},
            },
            messages=[
                {"role": "system", "content": JUDGE_PROMPT},
                {
                    "role": "user",
                    "content": f"<diagnosis>\n{diagnosis}\n</diagnosis>\n\n"
                    f'<passage chunk_id="{chunk_id}">\n{passage}\n</passage>',
                },
            ],
        )
        text = response.choices[0].message.content or ""
        try:
            data = json.loads(text)
            return Verdict(chunk_id, bool(data["supported"]), str(data.get("reason", ""))[:500])
        except (json.JSONDecodeError, KeyError, TypeError):
            # An unreadable verdict counts as unsupported: the metric must not flatter the agent.
            return Verdict(chunk_id, False, f"unparseable judge reply: {text[:200]}")
