from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256

from universal_kg.content_security import assess_retrieved_content
from universal_kg.domain import (
    CitationProvenance,
    Entity,
    Relationship,
    RetrievalProvenance,
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


class SearchService:
    def __init__(
        self,
        knowledge_store: KnowledgeStore | None = None,
        embedding_provider: EmbeddingProvider | None = None,
    ) -> None:
        self.knowledge_store = knowledge_store or get_knowledge_store()
        self.embedding_provider = embedding_provider or get_embedding_provider()

    async def search(self, request: SearchRequest) -> SearchResponse:
        vector = (await self.embedding_provider.embed([request.query]))[0]
        raw_hits = await self.knowledge_store.search(request.workspace_id, vector, request.limit)
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
        if request.include_graph:
            entities, relationships = await self.knowledge_store.graph_context(
                request.workspace_id, request.query
            )
            retrieval_values.extend(_graph_security_values(entities, relationships))

        return SearchResponse(
            query=request.query,
            hits=hits,
            related_entities=entities,
            relationships=relationships,
            security=assess_retrieved_content(retrieval_values),
        )
