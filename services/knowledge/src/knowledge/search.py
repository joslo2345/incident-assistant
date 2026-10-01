"""Retrieval: vector, keyword, hybrid (reciprocal rank fusion), and cross-encoder reranking.

- vector: cosine similarity on bge-small embeddings. Good at paraphrase ("GPU overheating"
  matches "thermal slowdown"), weak at exact identifiers ("XID 79" vs "XID 74" look alike).
- keyword: Postgres full-text search. Exact on identifiers and rare terms.
- hybrid: reciprocal rank fusion (RRF) of both lists. RRF uses ranks, not scores, so the two
  scales don't need calibrating against each other.
- rerank: a cross-encoder rescores the top fused candidates by reading query and passage together.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Literal

from knowledge.models import Embedder, Reranker

if TYPE_CHECKING:
    from knowledge.store import Conn

Mode = Literal["vector", "keyword", "hybrid", "rerank"]
RRF_K = 60  # standard constant from the RRF paper; dampens the weight of top ranks
CANDIDATES = 30  # per retriever, before fusion
RERANK_POOL = 20  # fused candidates the cross-encoder rescores


@dataclass(frozen=True)
class Hit:
    chunk_id: str
    doc_id: str
    doc_kind: Literal["runbook", "incident"]
    title: str
    heading: str
    text: str
    score: float


COLUMNS = "chunk_id, doc_id, doc_kind, title, heading, text"


def _filters(failure_type: str | None, doc_kind: str | None, start: int) -> tuple[str, list[Any]]:
    clauses, params = [], []
    if failure_type:
        params.append(failure_type)
        clauses.append(f"${start + len(params) - 1} = ANY(failure_types)")
    if doc_kind:
        params.append(doc_kind)
        clauses.append(f"doc_kind = ${start + len(params) - 1}")
    return ("AND " + " AND ".join(clauses)) if clauses else "", params


def keyword_query(text: str) -> str:
    """OR together the query's terms for to_tsquery, so partial matches still rank.

    websearch_to_tsquery ANDs every term, which returns nothing for a natural question whose
    words don't all appear in one chunk. ts_rank_cd still rewards chunks matching more terms.
    """
    terms = re.findall(r"[a-z0-9]+", text.lower())
    return " | ".join(dict.fromkeys(terms)) or "nothing"


class Searcher:
    def __init__(self, embedder: Embedder, reranker: Reranker | None) -> None:
        self.embedder = embedder
        self.reranker = reranker

    async def vector(self, conn: Conn, query: str, n: int, **filters: str | None) -> list[Hit]:
        where, params = _filters(filters.get("failure_type"), filters.get("doc_kind"), 3)
        rows = await conn.fetch(
            f"SELECT {COLUMNS}, 1 - (embedding <=> $1) AS score FROM kb_chunks "
            f"WHERE true {where} ORDER BY embedding <=> $1 LIMIT $2",
            self.embedder.embed_query(query), n, *params,
        )  # fmt: skip
        return [Hit(**dict(r)) for r in rows]

    async def keyword(self, conn: Conn, query: str, n: int, **filters: str | None) -> list[Hit]:
        where, params = _filters(filters.get("failure_type"), filters.get("doc_kind"), 3)
        rows = await conn.fetch(
            f"SELECT {COLUMNS}, ts_rank_cd(tsv, q) AS score "
            f"FROM kb_chunks, to_tsquery('english', $1) q WHERE tsv @@ q {where} "
            "ORDER BY score DESC LIMIT $2",
            keyword_query(query), n, *params,
        )  # fmt: skip
        return [Hit(**dict(r)) for r in rows]

    async def search(
        self, conn: Conn, query: str, k: int = 5, mode: Mode = "rerank", **filters: str | None
    ) -> list[Hit]:
        if mode == "vector":
            return await self.vector(conn, query, k, **filters)
        if mode == "keyword":
            return await self.keyword(conn, query, k, **filters)
        fused = fuse(
            await self.vector(conn, query, CANDIDATES, **filters),
            await self.keyword(conn, query, CANDIDATES, **filters),
        )
        if mode == "hybrid" or self.reranker is None:
            return fused[:k]
        pool = fused[:RERANK_POOL]
        scores = self.reranker.score(query, [h.text for h in pool])
        ranked = sorted(zip(scores, pool, strict=True), key=lambda p: p[0], reverse=True)
        return [replace(h, score=s) for s, h in ranked[:k]]


def fuse(*ranked_lists: list[Hit]) -> list[Hit]:
    """Reciprocal rank fusion: score = sum over lists of 1 / (RRF_K + rank)."""
    scores: dict[str, float] = {}
    hits: dict[str, Hit] = {}
    for hits_list in ranked_lists:
        for rank, h in enumerate(hits_list, start=1):
            scores[h.chunk_id] = scores.get(h.chunk_id, 0.0) + 1 / (RRF_K + rank)
            hits.setdefault(h.chunk_id, h)
    order = sorted(scores, key=lambda cid: scores[cid], reverse=True)
    return [replace(hits[cid], score=round(scores[cid], 5)) for cid in order]
