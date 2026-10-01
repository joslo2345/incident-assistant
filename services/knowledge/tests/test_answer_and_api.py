import asyncio
from collections.abc import Sequence

from fastapi.testclient import TestClient
from knowledge_testkit import hit

from incident_contracts.api import SearchRequest
from knowledge.answer import NOT_ENOUGH, Answer, ExtractiveAnswerer, validate
from knowledge.app import API_KEY_HEADER, Settings, create_app
from knowledge.search import Hit, Mode

KEY = "k"


def test_validate_drops_citations_to_chunks_not_provided() -> None:
    sources = [hit("rb-1#a"), hit("rb-2#b")]
    raw = {
        "insufficient_information": False,
        "answer": "Reboot the node.",
        "claims": [
            {"text": "Reboot the node.", "chunk_ids": ["rb-1#a", "made-up#x"]},
            {"text": "Invented claim.", "chunk_ids": ["made-up#y"]},
        ],
    }
    ans = validate(raw, sources, "claude", "m")
    assert [c.text for c in ans.claims] == ["Reboot the node."]
    assert [c.chunk_id for c in ans.claims[0].citations] == ["rb-1#a"]
    assert ans.claims[0].citations[0].doc_id == "rb-1"


def test_validate_fails_closed_when_nothing_is_supported() -> None:
    raw = {
        "insufficient_information": False,
        "answer": "Trust me.",
        "claims": [{"text": "x", "chunk_ids": []}],
    }
    ans = validate(raw, [hit("rb-1#a")], "claude", None)
    assert ans.insufficient_information and ans.claims == []


def test_extractive_answer_cites_the_best_passage() -> None:
    ans = asyncio.run(ExtractiveAnswerer().answer("q", [hit("rb-1#a"), hit("rb-2#b")]))
    assert not ans.insufficient_information
    assert ans.provider == "extractive"
    assert ans.claims[0].citations[0].chunk_id == "rb-1#a"


class FakeKB:
    def __init__(self, hits: list[Hit]) -> None:
        self.hits = hits

    async def search(self, req: SearchRequest, mode: Mode) -> list[Hit]:
        return self.hits[: req.k]

    async def get_chunk(self, chunk_id: str) -> Hit | None:
        return next((h for h in self.hits if h.chunk_id == chunk_id), None)


class RecordingAnswerer:
    def __init__(self) -> None:
        self.calls = 0

    async def answer(self, question: str, sources: Sequence[Hit]) -> Answer:
        self.calls += 1
        return await ExtractiveAnswerer().answer(question, sources)


def client(hits: list[Hit], answerer: RecordingAnswerer | None = None) -> TestClient:
    app = create_app(Settings(api_keys=frozenset({KEY}), min_relevance=0.0), FakeKB(hits), answerer)
    return TestClient(app)


def test_ask_returns_cited_answer_and_clickable_sources() -> None:
    with client([hit("rb-4#remediation", score=5.0)]) as c:
        r = c.post("/v1/ask", json={"query": "what is xid 79"}, headers={API_KEY_HEADER: KEY})
        body = r.json()
        assert r.status_code == 200 and not body["insufficient_information"]
        assert body["claims"][0]["citations"][0]["chunk_id"] == "rb-4#remediation"
        url = body["sources"][0]["url"]
        assert url == "/v1/chunks/rb-4%23remediation"  # '#' must not start a URL fragment
        assert c.get(url, headers={API_KEY_HEADER: KEY}).json()["chunk_id"] == "rb-4#remediation"


def test_gate_refuses_without_calling_the_model_when_retrieval_is_weak() -> None:
    answerer = RecordingAnswerer()
    with client([hit("rb-4#x", score=-5.0)], answerer) as c:
        body = c.post(
            "/v1/ask", json={"query": "warranty period?"}, headers={API_KEY_HEADER: KEY}
        ).json()
    assert body["insufficient_information"] and body["answer"] == NOT_ENOUGH
    assert body["provider"] == "gate" and answerer.calls == 0


def test_auth_required_and_unknown_chunk_404() -> None:
    with client([hit("rb-1#a")]) as c:
        assert c.post("/v1/search", json={"query": "fan"}).status_code == 401
        assert c.get("/v1/chunks/nope", headers={API_KEY_HEADER: KEY}).status_code == 404
        hits = c.post("/v1/search", json={"query": "fan", "k": 1}, headers={API_KEY_HEADER: KEY})
        assert hits.json()["hits"][0]["chunk_id"] == "rb-1#a"
