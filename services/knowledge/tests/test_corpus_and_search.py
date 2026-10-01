from pathlib import Path

from knowledge_testkit import hit

from knowledge.corpus import chunk_document, load_corpus, parse_front_matter, parse_markdown
from knowledge.search import fuse, keyword_query

REPO = Path(__file__).resolve().parents[3]


def test_front_matter_and_heading_chunks(tmp_path: Path) -> None:
    (tmp_path / "rb.md").write_text(
        "---\ndoc_id: rb-x\ntitle: Fan failure\nfailure_types: thermal_runaway, unknown\n"
        "version: 2\n---\n# Fan failure\n\n## Symptoms\nHot GPUs.\n\n## Remediation\nReplace fan.\n"
    )
    chunks = chunk_document(parse_markdown(tmp_path / "rb.md", "runbook"))
    assert [c.chunk_id for c in chunks] == ["rb-x#symptoms", "rb-x#remediation"]
    assert chunks[0].text.startswith("Fan failure > Symptoms")
    assert chunks[0].failure_types == ("thermal_runaway", "unknown")
    assert chunks[0].doc_version == "2"


def test_long_sections_split_on_paragraphs_with_unique_ids(tmp_path: Path) -> None:
    body = "\n\n".join(f"Paragraph {i} " + "word " * 60 for i in range(6))
    (tmp_path / "rb.md").write_text(f"---\ndoc_id: rb-y\ntitle: T\n---\n## Steps\n{body}\n")
    chunks = chunk_document(parse_markdown(tmp_path / "rb.md", "runbook"), max_words=150)
    assert len(chunks) > 1
    assert len({c.chunk_id for c in chunks}) == len(chunks)
    assert chunks[1].chunk_id == "rb-y#steps-2"


def test_short_incident_reports_stay_whole(tmp_path: Path) -> None:
    (tmp_path / "inc.md").write_text(
        "---\ndoc_id: inc-1\ntitle: T\n---\n## Summary\nXID 79.\n\n## Root cause\nRiser.\n"
    )
    chunks = chunk_document(parse_markdown(tmp_path / "inc.md", "incident"))
    assert [c.chunk_id for c in chunks] == ["inc-1#report"]
    assert "Root cause: Riser." in chunks[0].text


def test_unclosed_front_matter_is_an_error() -> None:
    try:
        parse_front_matter("---\ntitle: x\n")
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_repo_corpus_is_well_formed() -> None:
    chunks = list(load_corpus(REPO / "knowledge"))
    assert len({c.chunk_id for c in chunks}) == len(chunks)
    runbooks = {c.doc_id for c in chunks if c.doc_kind == "runbook"}
    assert len(runbooks) >= 20 and len({c.doc_id for c in chunks} - runbooks) >= 50
    assert all(c.failure_types for c in chunks if c.doc_kind == "runbook")


def test_rrf_rewards_agreement_between_retrievers() -> None:
    vector = [hit("a"), hit("b"), hit("c")]
    keyword = [hit("c"), hit("d"), hit("a")]
    order = [h.chunk_id for h in fuse(vector, keyword)]
    assert order[:2] == ["a", "c"]  # in both lists
    assert set(order) == {"a", "b", "c", "d"}


def test_keyword_query_ors_terms_and_strips_punctuation() -> None:
    assert keyword_query("What does XID 79 mean?") == "what | does | xid | 79 | mean"
    assert keyword_query("!!!") == "nothing"
    assert "'" not in keyword_query("it's GPU's fan; DROP TABLE kb_chunks")
