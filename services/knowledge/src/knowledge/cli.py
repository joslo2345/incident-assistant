"""Knowledge CLI.

knowledge ingest [--corpus knowledge]       # embed new/changed chunks, drop removed ones
knowledge eval [--questions eval/retrieval/questions.jsonl] [--out eval/reports/retrieval.md]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import time
from pathlib import Path
from typing import Any

import asyncpg

from knowledge.corpus import load_corpus
from knowledge.models import FastEmbedEmbedder, FastEmbedReranker
from knowledge.search import Mode, Searcher
from knowledge.store import setup_connection, sync

MODES: list[Mode] = ["vector", "keyword", "hybrid", "rerank"]
K = 5


def database_url() -> str:
    if url := os.environ.get("DATABASE_URL"):
        return url
    env_file = Path(__file__).resolve().parents[4] / "deploy" / ".env"
    env = dict(
        line.split("=", 1)
        for line in env_file.read_text().splitlines()
        if "=" in line and not line.startswith("#")
    )
    return f"postgresql://knowledge_service:{env['KNOWLEDGE_DB_PASSWORD']}@localhost:5432/telemetry"


async def ingest(corpus: Path) -> dict[str, Any]:
    started = time.monotonic()
    chunks = list(load_corpus(corpus))
    conn = await asyncpg.connect(database_url())
    try:
        await setup_connection(conn)
        result = await sync(conn, chunks, FastEmbedEmbedder())
    finally:
        await conn.close()
    return {**result.__dict__, "seconds": round(time.monotonic() - started, 1)}


def score_questions(
    ranked: dict[str, list[str]], questions: list[dict[str, Any]]
) -> dict[str, float]:
    """hit@1 / hit@5: any gold chunk at rank 1 / in the top 5. MRR over the first gold hit."""
    hits1 = hits5 = rr = 0.0
    answerable = [q for q in questions if q["gold"]]
    for q in answerable:
        ids = ranked[q["id"]]
        ranks = [i for i, cid in enumerate(ids[:K], start=1) if cid in q["gold"]]
        hits1 += bool(ranks and ranks[0] == 1)
        hits5 += bool(ranks)
        rr += 1 / ranks[0] if ranks else 0
    n = len(answerable)
    return {"hit@1": hits1 / n, "hit@5": hits5 / n, "mrr@5": rr / n}


def calibrate_gate(top_scores: dict[str, float], questions: list[dict[str, Any]]) -> dict[str, Any]:
    """Pick the reranker threshold that best separates answerable from unanswerable questions."""
    pos = sorted(top_scores[q["id"]] for q in questions if q["gold"])
    neg = sorted(top_scores[q["id"]] for q in questions if not q["gold"])
    best: tuple[float, float] | None = None
    for t in sorted(set(pos + neg)):
        acc = (sum(s >= t for s in pos) + sum(s < t for s in neg)) / (len(pos) + len(neg))
        if best is None or acc > best[1]:
            best = (t, acc)
    assert best is not None
    # Place the threshold midway into the gap below the chosen score.
    lower = max([s for s in pos + neg if s < best[0]], default=best[0] - 1)
    threshold = round((best[0] + lower) / 2, 2)
    return {
        "threshold": threshold,
        "answerable_kept": sum(s >= threshold for s in pos) / len(pos),
        "unanswerable_refused": sum(s < threshold for s in neg) / len(neg) if neg else None,
        "answerable_scores": {"min": round(pos[0], 2), "median": round(statistics.median(pos), 2)},
        "unanswerable_scores": {
            "max": round(neg[-1], 2),
            "median": round(statistics.median(neg), 2),
        }
        if neg
        else None,
    }


async def evaluate(questions_path: Path, out: Path | None) -> dict[str, Any]:
    questions = [json.loads(line) for line in questions_path.read_text().splitlines() if line]
    searcher = Searcher(FastEmbedEmbedder(), FastEmbedReranker())
    conn = await asyncpg.connect(database_url())
    results: dict[str, Any] = {}
    top_scores: dict[str, float] = {}
    misses: dict[str, list[str]] = {}
    try:
        await setup_connection(conn)
        for mode in MODES:
            ranked, latencies = {}, []
            for q in questions:
                t = time.perf_counter()
                hits = await searcher.search(conn, q["question"], K, mode)
                latencies.append(time.perf_counter() - t)
                ranked[q["id"]] = [h.chunk_id for h in hits]
                if mode == "rerank":
                    top_scores[q["id"]] = hits[0].score if hits else float("-inf")
            results[mode] = {
                **score_questions(ranked, questions),
                "latency_p50_ms": round(1000 * statistics.median(latencies), 1),
            }
            misses[mode] = [
                q["id"]
                for q in questions
                if q["gold"] and not set(ranked[q["id"]]) & set(q["gold"])
            ]
    finally:
        await conn.close()
    gate = calibrate_gate(top_scores, questions)
    report = {"k": K, "questions": len(questions), "modes": results, "misses": misses, "gate": gate}
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.with_suffix(".json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Knowledge base tools")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_ing = sub.add_parser("ingest")
    p_ing.add_argument("--corpus", type=Path, default=Path("knowledge"))
    p_ev = sub.add_parser("eval")
    p_ev.add_argument("--questions", type=Path, default=Path("eval/retrieval/questions.jsonl"))
    p_ev.add_argument("--out", type=Path, default=Path("eval/reports/retrieval.json"))
    args = parser.parse_args()
    if args.cmd == "ingest":
        print(json.dumps(asyncio.run(ingest(args.corpus)), indent=2))
    else:
        print(json.dumps(asyncio.run(evaluate(args.questions, args.out)), indent=2))
