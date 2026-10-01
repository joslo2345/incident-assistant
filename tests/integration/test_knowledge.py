"""Knowledge service against the live stack: retrieval quality gate and the HTTP contract."""

import json
import subprocess
import urllib.request
from typing import Any

from .conftest import REPO


def env() -> dict[str, str]:
    return dict(
        line.split("=", 1)
        for line in (REPO / "deploy" / ".env").read_text().splitlines()
        if "=" in line and not line.startswith("#")
    )


def call(path: str, body: dict[str, Any] | None = None) -> Any:
    req = urllib.request.Request(
        f"http://localhost:8001{path}",
        data=json.dumps(body).encode() if body else None,
        headers={"X-API-Key": env()["KNOWLEDGE_API_KEY"], "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def test_retrieval_quality_gate() -> None:
    out = REPO / "eval" / "runs" / "retrieval-ci.json"
    subprocess.run(
        ["uv", "run", "knowledge", "eval", "--out", str(out)],
        cwd=REPO, check=True, capture_output=True, text=True,
    )  # fmt: skip
    report = json.loads(out.read_text())
    print(json.dumps(report["modes"], indent=2))
    assert report["modes"]["rerank"]["hit@5"] >= 0.9
    assert report["modes"]["rerank"]["hit@5"] >= report["modes"]["hybrid"]["hit@5"]
    assert report["modes"]["hybrid"]["hit@5"] >= report["modes"]["vector"]["hit@5"]
    assert report["gate"]["unanswerable_refused"] == 1.0


def test_ask_cites_retrieved_chunks_and_links_resolve() -> None:
    body = call("/v1/ask", {"query": "GPU fell off the bus with XID 79, what now?"})
    assert not body["insufficient_information"]
    source_ids = {s["chunk_id"] for s in body["sources"]}
    cited = [c["chunk_id"] for claim in body["claims"] for c in claim["citations"]]
    assert cited and set(cited) <= source_ids
    first = body["sources"][0]
    assert call(first["url"])["chunk_id"] == first["chunk_id"]


def test_unanswerable_question_is_refused() -> None:
    body = call("/v1/ask", {"query": "How much does a replacement H100 cost?"})
    assert body["insufficient_information"] and body["claims"] == []
