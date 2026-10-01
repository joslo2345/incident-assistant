"""Ingestion into kb_chunks (incremental: unchanged chunks aren't re-embedded)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import asyncpg
from pgvector.asyncpg import register_vector

from knowledge.corpus import Chunk
from knowledge.models import Embedder

if TYPE_CHECKING:
    # asyncpg's classes are only generic in its type stubs, so this alias is type-check only.
    type Conn = asyncpg.Connection[Any] | asyncpg.pool.PoolConnectionProxy[Any]

UPSERT = """
INSERT INTO kb_chunks (chunk_id, doc_id, doc_kind, title, heading, text, failure_types,
    components, doc_version, source, content_hash, embedding)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
ON CONFLICT (chunk_id) DO UPDATE SET
    doc_id = EXCLUDED.doc_id, doc_kind = EXCLUDED.doc_kind, title = EXCLUDED.title,
    heading = EXCLUDED.heading, text = EXCLUDED.text, failure_types = EXCLUDED.failure_types,
    components = EXCLUDED.components, doc_version = EXCLUDED.doc_version,
    source = EXCLUDED.source, content_hash = EXCLUDED.content_hash,
    embedding = EXCLUDED.embedding, updated_at = now()
"""


async def setup_connection(conn: Conn) -> None:
    """Register the pgvector codec (use as asyncpg `init=` for pools)."""
    await register_vector(conn)  # type: ignore[arg-type]  # pool proxies work at runtime


@dataclass(frozen=True)
class SyncResult:
    total: int
    embedded: int
    unchanged: int
    deleted: int


async def sync(conn: Conn, chunks: Sequence[Chunk], embedder: Embedder) -> SyncResult:
    """Make kb_chunks match `chunks`: embed new or changed chunks, delete ones that are gone."""
    existing = {r["chunk_id"]: r["content_hash"] for r in await conn.fetch(
        "SELECT chunk_id, content_hash FROM kb_chunks"
    )}  # fmt: skip
    changed = [c for c in chunks if existing.get(c.chunk_id) != c.content_hash]
    vectors = embedder.embed_documents([c.text for c in changed]) if changed else []
    gone = sorted(set(existing) - {c.chunk_id for c in chunks})
    async with conn.transaction():
        await conn.executemany(
            UPSERT,
            [
                (c.chunk_id, c.doc_id, c.doc_kind, c.title, c.heading, c.text,
                 list(c.failure_types), list(c.components), c.doc_version, c.source,
                 c.content_hash, v)
                for c, v in zip(changed, vectors, strict=True)
            ],
        )  # fmt: skip
        if gone:
            await conn.execute("DELETE FROM kb_chunks WHERE chunk_id = ANY($1)", gone)
    return SyncResult(len(chunks), len(changed), len(chunks) - len(changed), len(gone))
