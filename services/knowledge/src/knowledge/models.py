"""Embedding and reranking models, behind small protocols so tests can use fakes.

Both run locally on CPU through fastembed (ONNX, no PyTorch), so retrieval costs nothing per query:
- embeddings: BAAI/bge-small-en-v1.5 (384 dimensions; queries get the model's search prefix)
- reranker: Xenova/ms-marco-MiniLM-L-6-v2 cross-encoder (scores query and passage together)
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from typing import Protocol

EMBED_MODEL = "BAAI/bge-small-en-v1.5"
RERANK_MODEL = "Xenova/ms-marco-MiniLM-L-6-v2"
EMBED_DIM = 384


class Embedder(Protocol):
    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...


class Reranker(Protocol):
    def score(self, query: str, passages: Sequence[str]) -> list[float]: ...


def _cache_dir() -> str | None:
    return os.environ.get("FASTEMBED_CACHE_DIR")


class FastEmbedEmbedder:
    def __init__(self, model: str = EMBED_MODEL) -> None:
        from fastembed import TextEmbedding

        self._model = TextEmbedding(model, cache_dir=_cache_dir())

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [v.tolist() for v in self._model.embed(list(texts))]

    def embed_query(self, text: str) -> list[float]:
        return next(iter(self._model.query_embed(text))).tolist()  # type: ignore[no-any-return]


class FastEmbedReranker:
    def __init__(self, model: str = RERANK_MODEL) -> None:
        from fastembed.rerank.cross_encoder import TextCrossEncoder

        self._model = TextCrossEncoder(model_name=model, cache_dir=_cache_dir())

    def score(self, query: str, passages: Sequence[str]) -> list[float]:
        return [float(s) for s in self._model.rerank(query, list(passages))]
