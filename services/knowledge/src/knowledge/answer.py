"""Grounded answers with citations.

Three safeguards keep answers verifiable:
1. Retrieval gate: if even the best reranked passage scores below `min_relevance`, answer
   "not enough information" without calling a model at all.
2. The model is told to answer only from the numbered sources and to cite chunk IDs per claim,
   returning structured JSON (enforced by the API's JSON-schema output).
3. Citations are checked after the fact: IDs that weren't in the sources are dropped, claims left
   without a valid citation are dropped, and an answer with no supported claims becomes
   "not enough information". The model can't cite something it wasn't given.

Providers: `ClaudeAnswerer` (when an API key is configured) and `ExtractiveAnswerer` (no model;
returns the best passage as a single cited claim, clearly labelled).
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import anthropic

from incident_contracts.api import AnswerClaim
from incident_contracts.incident import Citation
from knowledge.search import Hit

log = logging.getLogger("knowledge.answer")

NOT_ENOUGH = "Not enough information in the knowledge base to answer this reliably."
DEFAULT_MODEL = "claude-opus-5-5"
Effort = Literal["low", "medium", "high", "xhigh", "max"]

SYSTEM_PROMPT = """\
You answer questions from GPU datacenter operators using only the sources provided with each \
question: runbook sections and past incident reports from this team's knowledge base.

Rules:
- Use only facts stated in the sources. Do not add commands, thresholds, part names or procedures \
from general knowledge, even if you're confident they're right; operators act on these answers.
- Every claim must cite the chunk_id of each source that supports it. A claim that no source \
supports must be left out.
- If the sources don't contain enough to answer the question, set insufficient_information to \
true, say briefly what is missing in the answer, and return no claims. Partial answers are fine \
when the sources cover part of the question; say which part is not covered.
- Be concise and practical: what it means, what to check, what to do. Keep commands exactly as \
written in the sources.
- The answer field is the full answer in a few sentences. The claims list breaks the answer into \
individual statements, each with its citations.
"""

ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "insufficient_information": {"type": "boolean"},
        "answer": {"type": "string"},
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "chunk_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["text", "chunk_ids"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["insufficient_information", "answer", "claims"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class Answer:
    answer: str
    insufficient_information: bool
    claims: list[AnswerClaim]
    provider: str
    model: str | None = None


class Answerer(Protocol):
    async def answer(self, question: str, sources: Sequence[Hit]) -> Answer: ...


def format_sources(sources: Sequence[Hit]) -> str:
    return "\n\n".join(
        f'<source chunk_id="{h.chunk_id}" kind="{h.doc_kind}">\n{h.text}\n</source>'
        for h in sources
    )


def validate(
    raw: dict[str, Any], sources: Sequence[Hit], provider: str, model: str | None
) -> Answer:
    """Keep only citations to chunks that were actually provided; fail closed otherwise."""
    by_id = {h.chunk_id: h for h in sources}
    claims = []
    for c in raw.get("claims", []):
        cites = [
            Citation(chunk_id=cid, doc_id=by_id[cid].doc_id)
            for cid in dict.fromkeys(c.get("chunk_ids", []))
            if cid in by_id
        ]
        if cites and str(c.get("text", "")).strip():
            claims.append(AnswerClaim(text=str(c["text"]).strip()[:2000], citations=cites))
    insufficient = bool(raw.get("insufficient_information")) or not claims
    text = str(raw.get("answer", "")).strip()
    if insufficient:
        return Answer(text or NOT_ENOUGH, True, [], provider, model)
    return Answer(text, False, claims, provider, model)


class ExtractiveAnswerer:
    """No model: return the best passage, cited. Honest about what it is."""

    async def answer(self, question: str, sources: Sequence[Hit]) -> Answer:
        if not sources:
            return Answer(NOT_ENOUGH, True, [], "extractive")
        best = sources[0]
        body = best.text.split("\n\n", 1)[-1].strip()
        raw = {
            "insufficient_information": False,
            "answer": f"No language model is configured, so this is the most relevant passage "
            f"({best.title} > {best.heading}):\n\n{body}",
            "claims": [{"text": body, "chunk_ids": [best.chunk_id]}],
        }
        return validate(raw, sources, "extractive", None)


class ClaudeAnswerer:
    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        effort: Effort = "medium",
        client: anthropic.AsyncAnthropic | None = None,
    ) -> None:
        self.model = model
        self.effort = effort
        self.client = client or anthropic.AsyncAnthropic(timeout=60.0, max_retries=2)

    async def answer(self, question: str, sources: Sequence[Hit]) -> Answer:
        response = await self.client.beta.messages.create(
            model=self.model,
            max_tokens=4000,
            # On a safety-classifier false positive, retry server-side on the recommended
            # fallback model instead of failing the operator's question.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={
                "effort": self.effort,
                "format": {"type": "json_schema", "schema": ANSWER_SCHEMA},
            },
            # The system prompt is identical on every call; cache it.
            system=[
                {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
            ],
            messages=[
                {
                    "role": "user",
                    "content": f"Sources:\n\n{format_sources(sources)}\n\nQuestion: {question}",
                }
            ],
        )
        if response.stop_reason == "refusal":
            log.warning("model declined the question (request %s)", response._request_id)
            return Answer(NOT_ENOUGH, True, [], "claude", response.model)
        if response.stop_reason == "max_tokens":
            raise AnswerError("answer was cut off at max_tokens")
        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as exc:
            raise AnswerError(
                f"model returned invalid JSON (request {response._request_id})"
            ) from exc
        return validate(raw, sources, "claude", response.model)


class AnswerError(Exception):
    pass


async def answer_with_fallback(answerer: Answerer, question: str, sources: Sequence[Hit]) -> Answer:
    """Call the configured answerer; if the model is unavailable, degrade to extractive."""
    try:
        return await answerer.answer(question, sources)
    except (anthropic.RateLimitError, anthropic.APIConnectionError, AnswerError) as exc:
        log.warning("answer model unavailable (%s); returning extractive answer", exc)
    except anthropic.APIStatusError as exc:
        if exc.status_code < 500:
            raise  # 4xx other than 429 is a bug in our request; don't hide it
        log.warning("answer model error %s; returning extractive answer", exc.status_code)
    return await ExtractiveAnswerer().answer(question, sources)


def default_answerer() -> Answerer:
    """Claude when a credential is configured, otherwise extractive."""
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return ClaudeAnswerer(model=os.environ.get("KNOWLEDGE_MODEL", DEFAULT_MODEL))
    return ExtractiveAnswerer()
