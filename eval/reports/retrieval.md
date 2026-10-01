# Retrieval evaluation (A4)

Corpus: 22 runbooks (86 section chunks) + 64 synthetic past-incident reports (one chunk each),
150 chunks total. Questions: [eval/retrieval/questions.jsonl](../retrieval/questions.jsonl), 30
answerable questions labelled with the chunk(s) that answer them (any one counts as a hit) and
6 questions the knowledge base can't answer.

Reproduce: `make up && uv run knowledge eval`

## Results (top 5)

| Mode | Hit@5 (recall@5) | Hit@1 | MRR@5 | Latency p50 |
| --- | --- | --- | --- | --- |
| Vector (bge-small-en-v1.5, pgvector HNSW) | 73% | 47% | 0.57 | 4 ms |
| Keyword (Postgres full-text) | 70% | 43% | 0.55 | 5 ms |
| Hybrid (reciprocal rank fusion) | 83% | 47% | 0.61 | 9 ms |
| **Hybrid + cross-encoder rerank** | **100%** | **80%** | **0.87** | 235 ms |

Hit@5 is the share of answerable questions with a correct chunk in the top 5, which is how the
plan's recall@5 is measured here.

## Why each step helps

- **Vector search misses exact identifiers.** "XID 48", "XID 94 vs 95" and "a 420 W power limit"
  fail, because embeddings treat similar codes as near-synonyms.
- **Keyword search misses paraphrases.** "Evict workloads from a Kubernetes node" doesn't match a
  section that says "drain", and natural questions about past incidents score poorly.
- **The two fail on different questions,** so fusing them recovers most misses (83%). Fusion uses
  ranks, not scores, so the two score scales don't need calibrating.
- **The cross-encoder** reads the question and passage together. It fixes every remaining miss
  and moves the right chunk to rank 1 for 80% of questions. It runs locally on CPU (ONNX), so it
  adds about 0.2 s and no cost.

## Refusing when the sources don't cover the question

The best reranked passage scores at least +1.47 for every answerable question and at most −2.19 for
every unanswerable one. With the threshold set in the middle of that gap (−0.4), `/v1/ask` refuses all
6 unanswerable questions *before calling any model* and keeps all 30 answerable ones. A second
layer applies to model answers: citations to chunks that weren't provided are dropped, and an answer
with no supported claim becomes "not enough information".

## Caveats

- The questions were written by someone who knows the corpus, so these numbers are optimistic.
  Real operator questions (from the A7 feedback loop) are the next test set.
- The refusal threshold was chosen on the same 36 questions it's measured on. The gap is wide
  (3.7 logits), but it should be re-checked when the corpus grows.
- Answer quality with Claude (faithfulness of the generated text) isn't measured yet, because no
  API key was configured. That moves to A6, where an LLM judge checks citation support.
