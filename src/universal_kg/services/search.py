from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from hashlib import sha256
import re

from universal_kg.access import ensure_workspace_access
from universal_kg.content_security import assess_retrieved_content
from universal_kg.domain import (
    AccessContext,
    CitationProvenance,
    ConflictCandidate,
    DuplicateCandidate,
    Entity,
    Relationship,
    RetrievalDiagnostics,
    RetrievalProvenance,
    RetrievalRanking,
    SearchHit,
    SearchRequest,
    SearchResponse,
)
from universal_kg.processing.embeddings import EmbeddingProvider, get_embedding_provider
from universal_kg.storage.base import KnowledgeStore
from universal_kg.storage.factory import get_knowledge_store


def _metadata_values(metadata: dict[str, object]) -> list[str]:
    return [str(value) for value in metadata.values()]


def _metadata_string(metadata: dict[str, object], key: str) -> str | None:
    value = metadata.get(key)
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _metadata_positive_int(metadata: dict[str, object], key: str) -> int | None:
    value = metadata.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value


def _hit_security_values(hit: SearchHit) -> list[str]:
    return [hit.title, hit.text, hit.source, *_metadata_values(hit.metadata)]


def _citation(hit: SearchHit, workspace_id: str, retrieved_at: datetime) -> CitationProvenance:
    return CitationProvenance(
        workspace_id=workspace_id,
        source=hit.source,
        document_id=hit.document_id,
        chunk_id=hit.chunk_id,
        source_uri=_metadata_string(hit.metadata, "source_uri"),
        source_version=_metadata_string(hit.metadata, "source_version"),
        page_start=_metadata_positive_int(hit.metadata, "page_start"),
        page_end=_metadata_positive_int(hit.metadata, "page_end"),
        retrieved_at=retrieved_at,
        content_sha256=sha256(hit.text.encode("utf-8")).hexdigest(),
    )


def _graph_security_values(
    entities: list[Entity], relationships: list[Relationship]
) -> list[str]:
    values: list[str] = []
    for entity in entities:
        values.extend([entity.name, str(entity.type), *_metadata_values(entity.metadata)])
    for relationship in relationships:
        values.extend(
            [
                relationship.subject,
                relationship.predicate,
                relationship.object,
                *_metadata_values(relationship.metadata),
            ]
        )
    return values


_QUERY_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")


def _query_terms(value: str) -> list[str]:
    return [match.group(0).lower() for match in _QUERY_TOKEN.finditer(value)]


def _reciprocal_rank(rank: int, constant: int = 60) -> float:
    return 1.0 / (constant + rank)


def _rerank_score(query: str, hit: SearchHit) -> float:
    query_normalised = " ".join(_query_terms(query))
    title_normalised = " ".join(_query_terms(hit.title))
    text_normalised = " ".join(_query_terms(hit.text))
    terms = set(_query_terms(query))
    if not terms:
        return 0.0

    available = set(_query_terms(hit.title + " " + hit.text))
    coverage = len(terms & available) / len(terms)
    exact_title = 1.0 if query_normalised and query_normalised in title_normalised else 0.0
    exact_text = 1.0 if query_normalised and query_normalised in text_normalised else 0.0
    return round(min(1.0, 0.65 * coverage + 0.2 * exact_title + 0.15 * exact_text), 6)


def _duplicate_candidates(hits: list[SearchHit]) -> list[DuplicateCandidate]:
    grouped: dict[str, list[SearchHit]] = defaultdict(list)
    for hit in hits:
        digest = sha256(hit.text.encode("utf-8")).hexdigest()
        grouped[digest].append(hit)

    duplicates: list[DuplicateCandidate] = []
    for digest, grouped_hits in sorted(grouped.items()):
        document_ids = sorted({hit.document_id for hit in grouped_hits})
        if len(document_ids) < 2:
            continue
        duplicates.append(
            DuplicateCandidate(
                content_sha256=digest,
                document_ids=document_ids,
                chunk_ids=sorted({hit.chunk_id for hit in grouped_hits}),
            )
        )
    return duplicates


def _conflict_candidates(relationships: list[Relationship]) -> list[ConflictCandidate]:
    grouped: dict[tuple[str, str], list[Relationship]] = defaultdict(list)
    for relationship in relationships:
        grouped[
            (relationship.subject.casefold(), relationship.predicate.casefold())
        ].append(relationship)

    conflicts: list[ConflictCandidate] = []
    for _, grouped_relationships in sorted(grouped.items()):
        objects = sorted({relationship.object for relationship in grouped_relationships})
        if len(objects) < 2:
            continue
        first = grouped_relationships[0]
        conflicts.append(
            ConflictCandidate(
                subject=first.subject,
                predicate=first.predicate,
                objects=objects,
                relationship_ids=sorted(
                    {relationship.id for relationship in grouped_relationships}
                ),
            )
        )
    return conflicts


def _fuse_hits(
    query: str,
    vector_hits: list[SearchHit],
    lexical_hits: list[SearchHit],
    limit: int,
    rerank: bool,
) -> list[SearchHit]:
    by_chunk: dict[str, SearchHit] = {}
    vector_rank: dict[str, int] = {}
    lexical_rank: dict[str, int] = {}

    for rank, hit in enumerate(vector_hits, start=1):
        by_chunk.setdefault(hit.chunk_id, hit)
        vector_rank[hit.chunk_id] = rank
    for rank, hit in enumerate(lexical_hits, start=1):
        by_chunk.setdefault(hit.chunk_id, hit)
        lexical_rank[hit.chunk_id] = rank

    fused: list[SearchHit] = []
    vector_scores = {hit.chunk_id: hit.score for hit in vector_hits}
    lexical_scores = {hit.chunk_id: hit.score for hit in lexical_hits}
    for chunk_id, hit in by_chunk.items():
        fusion_score = 0.0
        if chunk_id in vector_rank:
            fusion_score += _reciprocal_rank(vector_rank[chunk_id])
        if chunk_id in lexical_rank:
            fusion_score += _reciprocal_rank(lexical_rank[chunk_id])
        rerank_score = _rerank_score(query, hit) if rerank else 0.0
        final_score = fusion_score + 0.01 * rerank_score
        fused.append(
            hit.model_copy(
                update={
                    "score": final_score,
                    "ranking": RetrievalRanking(
                        vector_score=vector_scores.get(chunk_id),
                        lexical_score=lexical_scores.get(chunk_id),
                        fusion_score=round(fusion_score, 8),
                        rerank_score=rerank_score,
                        final_score=round(final_score, 8),
                    ),
                }
            )
        )

    fused.sort(
        key=lambda hit: (
            -(hit.ranking.final_score if hit.ranking else hit.score),
            hit.chunk_id,
        )
    )
    return fused[:limit]


class SearchService:
    def __init__(
        self,
        knowledge_store: KnowledgeStore | None = None,
        embedding_provider: EmbeddingProvider | None = None,
    ) -> None:
        self.knowledge_store = knowledge_store or get_knowledge_store()
        self.embedding_provider = embedding_provider or get_embedding_provider()

    async def search(self, request: SearchRequest, access: AccessContext) -> SearchResponse:
        ensure_workspace_access(request.workspace_id, access)
        candidate_limit = min(200, request.limit * request.candidate_multiplier)

        vector_hits: list[SearchHit] = []
        lexical_hits: list[SearchHit] = []
        if request.retrieval_mode in {"vector", "hybrid"}:
            vector = (await self.embedding_provider.embed([request.query]))[0]
            vector_hits = await self.knowledge_store.search(
                request.workspace_id,
                vector,
                candidate_limit,
                access,
            )
        if request.retrieval_mode in {"lexical", "hybrid"}:
            lexical_hits = await self.knowledge_store.lexical_search(
                request.workspace_id,
                request.query,
                candidate_limit,
                access,
            )

        if request.retrieval_mode == "vector":
            raw_hits = _fuse_hits(
                request.query,
                vector_hits,
                [],
                request.limit,
                request.rerank,
            )
        elif request.retrieval_mode == "lexical":
            raw_hits = _fuse_hits(
                request.query,
                [],
                lexical_hits,
                request.limit,
                request.rerank,
            )
        else:
            raw_hits = _fuse_hits(
                request.query,
                vector_hits,
                lexical_hits,
                request.limit,
                request.rerank,
            )

        retrieved_at = datetime.now(UTC)
        hits: list[SearchHit] = []
        retrieval_values: list[str] = []
        for hit in raw_hits:
            security_values = _hit_security_values(hit)
            retrieval_values.extend(security_values)
            hits.append(
                hit.model_copy(
                    update={
                        "provenance": RetrievalProvenance(
                            workspace_id=request.workspace_id,
                            source=hit.source,
                            document_id=hit.document_id,
                            chunk_id=hit.chunk_id,
                        ),
                        "citation": _citation(hit, request.workspace_id, retrieved_at),
                        "security": assess_retrieved_content(security_values),
                    }
                )
            )

        entities: list[Entity] = []
        relationships: list[Relationship] = []
        if request.include_graph and request.graph_depth > 0:
            entities, relationships = await self.knowledge_store.graph_context(
                request.workspace_id,
                request.query,
                access,
                request.graph_depth,
            )
            retrieval_values.extend(_graph_security_values(entities, relationships))

        diagnostics = RetrievalDiagnostics(
            retrieval_mode=request.retrieval_mode,
            vector_candidates=len(vector_hits),
            lexical_candidates=len(lexical_hits),
            fused_candidates=len({hit.chunk_id for hit in [*vector_hits, *lexical_hits]}),
            graph_depth=request.graph_depth if request.include_graph else 0,
            duplicates=_duplicate_candidates(hits),
            conflicts=_conflict_candidates(relationships),
        )

        return SearchResponse(
            query=request.query,
            hits=hits,
            related_entities=entities,
            relationships=relationships,
            security=assess_retrieved_content(retrieval_values),
            diagnostics=diagnostics,
        )
