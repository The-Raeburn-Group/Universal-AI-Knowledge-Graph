# Hybrid Retrieval, Reranking and Graph Quality

The Knowledge Graph exposes three retrieval modes through `SearchRequest.retrieval_mode`:

- `vector` — tenant/ACL-filtered pgvector cosine retrieval;
- `lexical` — tenant/ACL-filtered PostgreSQL full-text search;
- `hybrid` — reciprocal-rank fusion of both channels followed by an optional transparent reranker.

`hybrid` is the default. `vector` remains available for baseline comparison and rollback.

## PostgreSQL lexical retrieval

PostgreSQL uses `websearch_to_tsquery` against a `simple` text-search configuration. A versioned Alembic migration creates a GIN index on the chunk text search vector.

The document and chunk access predicates are applied in the lexical query itself. Restricted text therefore does not enter the candidate set or influence the returned lexical ranking for a caller that cannot read it.

The in-memory backend uses the same public store contract with a deterministic BM25-style scorer so unit tests and local development exercise the lexical path without requiring PostgreSQL.

## Hybrid fusion

Vector and lexical candidate pools are fetched independently after access filtering. Candidates are merged by chunk ID with reciprocal-rank fusion.

Each returned hit exposes:

- raw vector score when present;
- raw lexical score when present;
- reciprocal-rank fusion score;
- reranker score; and
- final score.

The deterministic v1 reranker uses query-token coverage plus exact phrase matches in title/text. It is intentionally inspectable and cheap. It is not represented as a learned cross-encoder.

`candidate_multiplier` controls pre-fusion depth and is bounded to 1–10. The final `limit` remains bounded to 50.

## Retrieval-quality benchmark

`make retrieval-bench` runs a deterministic regression in which the vector channel deliberately prefers a semantically nearby but incorrect record while the lexical channel contains an exact authoritative identifier.

The gate requires hybrid+rerrank to recover the exact authoritative result at rank 1 and forbids regression below the vector baseline. This is an engineering seed benchmark, not a production retrieval-quality claim.

## Bounded graph traversal

`graph_depth` is bounded to 0–3. Each traversal hop re-applies:

- workspace scope;
- parent document lifecycle state;
- parent document ACL; and
- entity/relationship ACL.

The service caps graph context at 20 entities and 50 relationships. Traversal never ignores source-document permissions simply because a child graph object has drifted to a broader ACL.

## Knowledge hygiene diagnostics

Search responses include non-destructive diagnostics:

- exact chunk-content duplicate candidates grouped by the SHA-256 of each returned chunk;
- exact document duplicate candidates grouped separately by the server-generated SHA-256 of the complete ingested document body; and
- graph conflict candidates when the same subject/predicate is linked to multiple distinct objects.

Conflict candidates are always labelled `review_required`. The system does not automatically choose a winning fact.

These diagnostics are scoped to the retrieved/accessible evidence. They are not a global deduplication or truth-resolution process.

## Current limits

This implementation does not yet claim:

- a learned cross-encoder/LLM reranker;
- private held-out retrieval-quality benchmarks;
- production-scale lexical/vector latency thresholds;
- semantic near-duplicate detection;
- automatic conflict resolution;
- source-priority/effective-date truth adjudication;
- live enterprise connector ACL-refresh behavior; or
- hosted staging/production deployment evidence.

Those remain separate delivery gates.
