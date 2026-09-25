from __future__ import annotations

import math
import re
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

_LEXICAL_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")


def _lexical_terms(value: str) -> list[str]:
    return [match.group(0).lower() for match in _LEXICAL_TOKEN.finditer(value)]


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

    def _document_allows(
        self,
        document_id: str | None,
        workspace_id: str,
        access: AccessContext,
    ) -> bool:
        if not document_id:
            return False
        document = self.documents.get(document_id)
        return bool(
            document
            and document.workspace_id == workspace_id
            and document.deleted_at is None
            and access_allows(document.access, access)
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
                    source_acl_ref=document.access.source_acl_ref,
                )
            )
        return hits

    async def lexical_search(
        self,
        workspace_id: str,
        query: str,
        limit: int,
        access: AccessContext,
    ) -> list[SearchHit]:
        query_terms = _lexical_terms(query)
        if not query_terms:
            return []

        accessible: list[tuple[Chunk, Document, list[str]]] = []
        for chunk in self.chunks.values():
            if chunk.workspace_id != workspace_id:
                continue
            document = self.documents.get(chunk.document_id)
            if not document or document.deleted_at is not None:
                continue
            if not access_allows(document.access, access) or not access_allows(
                chunk.access, access
            ):
                continue
            terms = _lexical_terms(chunk.text)
            accessible.append((chunk, document, terms))

        if not accessible:
            return []

        document_frequency = {
            term: sum(1 for _, _, terms in accessible if term in set(terms))
            for term in query_terms
        }
        average_length = sum(len(terms) for _, _, terms in accessible) / len(accessible)
        k1 = 1.2
        b = 0.75
        scored: list[tuple[float, Chunk, Document]] = []
        for chunk, document, terms in accessible:
            if not terms:
                continue
            score = 0.0
            for term in query_terms:
                frequency = terms.count(term)
                if frequency == 0:
                    continue
                df = document_frequency[term]
                idf = math.log(1.0 + (len(accessible) - df + 0.5) / (df + 0.5))
                denominator = frequency + k1 * (
                    1.0 - b + b * len(terms) / max(1.0, average_length)
                )
                score += idf * (frequency * (k1 + 1.0)) / denominator
            if score > 0:
                scored.append((score, chunk, document))

        scored.sort(key=lambda item: (-item[0], item[1].id))
        return [
            SearchHit(
                document_id=document.id,
                chunk_id=chunk.id,
                title=document.title,
                text=chunk.text,
                score=score,
                source=document.source,
                metadata=document.metadata | chunk.metadata,
                source_acl_ref=document.access.source_acl_ref,
            )
            for score, chunk, document in scored[:limit]
        ]

    async def graph_context(
        self,
        workspace_id: str,
        query: str,
        access: AccessContext,
        depth: int = 1,
    ) -> tuple[list[Entity], list[Relationship]]:
        if depth < 0 or depth > 3:
            raise ValueError("graph_depth_out_of_range")
        tokens = {token.lower() for token in query.split() if len(token) > 2}
        if not tokens or depth == 0:
            return [], []

        allowed_entities = [
            entity
            for entity in self.entities
            if entity.workspace_id == workspace_id
            and self._document_allows(entity.document_id, workspace_id, access)
            and access_allows(entity.access, access)
        ]
        by_name: dict[str, list[Entity]] = {}
        for entity in allowed_entities:
            by_name.setdefault(entity.name, []).append(entity)

        seed_entities = sorted(
            (
                entity
                for entity in allowed_entities
                if any(token in entity.name.lower() for token in tokens)
            ),
            key=lambda item: (item.name, item.id),
        )[:20]
        selected: dict[str, Entity] = {
            entity.id: entity for entity in seed_entities
        }
        frontier = {entity.name for entity in seed_entities}
        relationship_by_id: dict[str, Relationship] = {}

        for _ in range(depth):
            if not frontier or len(relationship_by_id) >= 50:
                break
            next_names: set[str] = set()
            for relationship in self.relationships:
                if relationship.id in relationship_by_id:
                    continue
                if relationship.workspace_id != workspace_id:
                    continue
                if not self._document_allows(
                    relationship.document_id, workspace_id, access
                ) or not access_allows(relationship.access, access):
                    continue
                if relationship.subject not in frontier and relationship.object not in frontier:
                    continue
                relationship_by_id[relationship.id] = relationship
                if relationship.subject in by_name:
                    next_names.add(relationship.subject)
                if relationship.object in by_name:
                    next_names.add(relationship.object)
                if len(relationship_by_id) >= 50:
                    break

            for name in sorted(next_names):
                for entity in by_name.get(name, []):
                    if len(selected) >= 20:
                        break
                    selected.setdefault(entity.id, entity)
            frontier = next_names

        entities = sorted(selected.values(), key=lambda item: (item.name, item.id))[:20]
        relationships = sorted(
            relationship_by_id.values(),
            key=lambda item: (item.subject, item.predicate, item.object, item.id),
        )[:50]
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
