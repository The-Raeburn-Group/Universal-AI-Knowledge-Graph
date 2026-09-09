from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from universal_kg.access import access_allows
from universal_kg.domain import (
    AccessContext,
    Chunk,
    Document,
    Entity,
    Relationship,
    SearchHit,
    TombstoneDocumentResponse,
)


def cosine(a: list[float], b: list[float]) -> float:
    numerator = sum(x * y for x, y in zip(a, b, strict=False))
    denominator = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    if denominator == 0:
        return 0.0
    return numerator / denominator


@dataclass
class MemoryKnowledgeStore:
    documents: dict[str, Document] = field(default_factory=dict)
    chunks: dict[str, Chunk] = field(default_factory=dict)
    vectors: dict[str, list[float]] = field(default_factory=dict)
    entities: list[Entity] = field(default_factory=list)
    relationships: list[Relationship] = field(default_factory=list)

    async def upsert_document(self, document: Document) -> None:
        self.documents[document.id] = document

    async def upsert_chunks(self, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        for chunk, vector in zip(chunks, vectors, strict=True):
            self.chunks[chunk.id] = chunk
            self.vectors[chunk.id] = vector

    async def upsert_graph(self, entities: list[Entity], relationships: list[Relationship]) -> None:
        self.entities.extend(entities)
        self.relationships.extend(relationships)

    def _is_active_document(self, document_id: str | None, workspace_id: str) -> bool:
        if not document_id:
            return False
        document = self.documents.get(document_id)
        return bool(
            document
            and document.workspace_id == workspace_id
            and document.deleted_at is None
        )

    async def search(
        self,
        workspace_id: str,
        query_vector: list[float],
        limit: int,
        access: AccessContext,
    ) -> list[SearchHit]:
        scored: list[tuple[float, Chunk]] = []
        for chunk_id, chunk in self.chunks.items():
            if chunk.workspace_id != workspace_id:
                continue
            document = self.documents.get(chunk.document_id)
            if not document or document.deleted_at is not None:
                continue
            if not access_allows(document.access, access) or not access_allows(
                chunk.access, access
            ):
                continue
            scored.append((cosine(query_vector, self.vectors[chunk_id]), chunk))
        scored.sort(key=lambda item: item[0], reverse=True)
        hits: list[SearchHit] = []
        for score, chunk in scored[:limit]:
            document = self.documents[chunk.document_id]
            hits.append(
                SearchHit(
                    document_id=document.id,
                    chunk_id=chunk.id,
                    title=document.title,
                    text=chunk.text,
                    score=score,
                    source=document.source,
                    metadata=document.metadata | chunk.metadata,
                )
            )
        return hits

    async def graph_context(
        self,
        workspace_id: str,
        query: str,
        access: AccessContext,
    ) -> tuple[list[Entity], list[Relationship]]:
        tokens = {token.lower() for token in query.split() if len(token) > 2}
        entities = [
            entity
            for entity in self.entities
            if entity.workspace_id == workspace_id
            and self._is_active_document(entity.document_id, workspace_id)
            and access_allows(entity.access, access)
            and any(token in entity.name.lower() for token in tokens)
        ][:20]
        names = {entity.name for entity in entities}
        relationships = [
            rel
            for rel in self.relationships
            if rel.workspace_id == workspace_id
            and self._is_active_document(rel.document_id, workspace_id)
            and access_allows(rel.access, access)
            and (rel.subject in names or rel.object in names)
        ][:50]
        return entities, relationships

    async def tombstone_document(
        self,
        workspace_id: str,
        document_id: str,
        reason: str,
        deleted_at: datetime,
        purge_after: datetime,
    ) -> TombstoneDocumentResponse | None:
        document = self.documents.get(document_id)
        if not document or document.workspace_id != workspace_id:
            return None
        if document.deleted_at is None:
            document.deleted_at = deleted_at
            document.purge_after = purge_after
            document.deletion_reason = reason
        return TombstoneDocumentResponse(
            document_id=document.id,
            workspace_id=document.workspace_id,
            deleted_at=document.deleted_at or deleted_at,
            purge_after=document.purge_after or purge_after,
            reason=document.deletion_reason or reason,
        )

    async def run_retention(
        self,
        workspace_id: str,
        as_of: datetime,
        purge_grace_days: int,
    ) -> tuple[int, int]:
        tombstoned = 0
        for document in self.documents.values():
            if (
                document.workspace_id == workspace_id
                and document.deleted_at is None
                and document.retention_until is not None
                and document.retention_until <= as_of
            ):
                document.deleted_at = as_of
                document.purge_after = as_of + timedelta(days=purge_grace_days)
                document.deletion_reason = "retention_expired"
                tombstoned += 1

        purge_ids = {
            document.id
            for document in self.documents.values()
            if document.workspace_id == workspace_id
            and document.deleted_at is not None
            and document.purge_after is not None
            and document.purge_after <= as_of
        }
        if purge_ids:
            chunk_ids = {
                chunk_id
                for chunk_id, chunk in self.chunks.items()
                if chunk.document_id in purge_ids
            }
            for document_id in purge_ids:
                self.documents.pop(document_id, None)
            for chunk_id in chunk_ids:
                self.chunks.pop(chunk_id, None)
                self.vectors.pop(chunk_id, None)
            self.entities = [
                entity for entity in self.entities if entity.document_id not in purge_ids
            ]
            self.relationships = [
                relationship
                for relationship in self.relationships
                if relationship.document_id not in purge_ids
            ]

        return tombstoned, len(purge_ids)

    async def check_ready(self) -> None:
        return None

    async def close(self) -> None:
        return None


store = MemoryKnowledgeStore()
