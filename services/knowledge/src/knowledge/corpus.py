"""Corpus loading and chunking.

Documents are Markdown (with a small front-matter block) or PDF. A past-incident report short enough
to fit in one chunk stays whole, so its symptoms, root cause and fix are retrieved together.
Runbooks are chunked by heading, so citations point at the exact section:
each `##` section becomes a chunk whose text starts with "<title> > <heading>", so a chunk read
out of context still says what it's about. Sections longer than `max_words` are split on
paragraph boundaries. Chunk IDs are `<doc_id>#<heading-slug>[-n]`, stable across re-ingestion as
long as headings don't change, so citations and evaluation labels stay valid.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

DocKind = Literal["runbook", "incident"]
MAX_WORDS = 220


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    doc_id: str
    doc_kind: DocKind
    title: str
    heading: str
    text: str  # what gets embedded and shown: "title > heading" line, then the section body
    failure_types: tuple[str, ...]
    components: tuple[str, ...]
    doc_version: str
    source: str | None
    content_hash: str = field(default="")


@dataclass(frozen=True)
class Document:
    doc_id: str
    kind: DocKind
    title: str
    meta: dict[str, str]
    sections: list[tuple[str, str]]  # (heading, body)
    path: Path


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "section"


def _split_list(value: str) -> tuple[str, ...]:
    return tuple(v.strip() for v in value.split(",") if v.strip())


def parse_front_matter(text: str) -> tuple[dict[str, str], str]:
    """`---` delimited `key: value` lines at the top of the file. Returns (meta, rest)."""
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end == -1:
        raise ValueError("front matter is not closed with '---'")
    meta = {}
    for line in text[4:end].splitlines():
        if line.strip():
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
    return meta, text[end + 5 :]


def parse_markdown(path: Path, kind: DocKind) -> Document:
    meta, body = parse_front_matter(path.read_text())
    title = meta.get("title", path.stem)
    sections: list[tuple[str, str]] = []
    heading, lines = "Overview", list[str]()
    for line in body.splitlines():
        if line.startswith("# "):  # document title line; front matter already has it
            continue
        if line.startswith("## "):
            if "".join(lines).strip():
                sections.append((heading, "\n".join(lines).strip()))
            heading, lines = line[3:].strip(), []
        else:
            lines.append(line)
    if "".join(lines).strip():
        sections.append((heading, "\n".join(lines).strip()))
    return Document(meta.get("doc_id", path.stem), kind, title, meta, sections, path)


def parse_pdf(path: Path, kind: DocKind) -> Document:
    """PDFs have no reliable headings, so each page is a section."""
    from pypdf import PdfReader

    reader = PdfReader(path)
    sections = [
        (f"Page {i + 1}", (page.extract_text() or "").strip())
        for i, page in enumerate(reader.pages)
    ]
    title = (reader.metadata.title if reader.metadata else None) or path.stem
    return Document(path.stem, kind, title, {}, [s for s in sections if s[1]], path)


def _paragraph_groups(body: str, max_words: int) -> list[str]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    groups: list[str] = []
    current: list[str] = []
    words = 0
    for p in paragraphs:
        n = len(p.split())
        if current and words + n > max_words:
            groups.append("\n\n".join(current))
            current, words = [], 0
        current.append(p)
        words += n
    if current:
        groups.append("\n\n".join(current))
    return groups


def chunk_document(doc: Document, max_words: int = MAX_WORDS) -> list[Chunk]:
    chunks = []
    seen: dict[str, int] = {}
    sections = doc.sections
    if doc.kind == "incident" and sum(len(b.split()) for _, b in sections) <= max_words:
        sections = [("Report", "\n\n".join(f"{h}: {b}" for h, b in sections))]
    for heading, body in sections:
        for part in _paragraph_groups(body, max_words):
            base = f"{doc.doc_id}#{slug(heading)}"
            seen[base] = seen.get(base, 0) + 1
            chunk_id = base if seen[base] == 1 else f"{base}-{seen[base]}"
            text = f"{doc.title} > {heading}\n\n{part}"
            chunks.append(
                Chunk(
                    chunk_id=chunk_id,
                    doc_id=doc.doc_id,
                    doc_kind=doc.kind,
                    title=doc.title,
                    heading=heading,
                    text=text,
                    failure_types=_split_list(doc.meta.get("failure_types", "")),
                    components=_split_list(doc.meta.get("components", "")),
                    doc_version=doc.meta.get("version", "1"),
                    source=doc.meta.get("source"),
                    content_hash=hashlib.sha256(text.encode()).hexdigest()[:16],
                )
            )
    return chunks


def load_corpus(root: Path) -> Iterator[Chunk]:
    """root/runbooks/* are runbooks, root/incidents/* are past incidents (.md or .pdf)."""
    for sub, kind in (("runbooks", "runbook"), ("incidents", "incident")):
        for path in sorted((root / sub).glob("*")):
            if path.suffix == ".md":
                doc = parse_markdown(path, kind)  # type: ignore[arg-type]
            elif path.suffix == ".pdf":
                doc = parse_pdf(path, kind)  # type: ignore[arg-type]
            else:
                continue
            yield from chunk_document(doc)
