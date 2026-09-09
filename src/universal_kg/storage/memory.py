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
from universal_kg.recovery import DeletionLedgerEntry


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
    deletion_events: dict[tuple[str, str], DeletionLedgerEntry] = field(default_factory=dict)

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

    def _record_deletion(
        self,
        document: Document,
        reason: str,
        deleted_at: datetime,
        purge_after: datetime,
    ) -> DeletionLedgerEntry:
        key = (document.workspace_id, document.id)
        existing = self.deletion_events.get(key)
        if existing is not None:
            return existing
        entry = DeletionLedgerEntry(
            workspace_id=document.workspace_id,
            document_id=document.id,
            source=document.source,
            external_id=document.external_id,
            deleted_at=deleted_at,
            purge_after=purge_after,
            reason=reason,
        )
        self.deletion_events[key] = entry
        return entry

    def _purge_documents(self, document_ids: set[str]) -> None:
        if not document_ids:
            return
        chunk_ids = {
            chunk_id
            for chunk_id, chunk in self.chunks.items()
            if chunk.document_id in document_ids
        }
        for document_id in document_ids:
            self.documents.pop(document_id, None)
        for chunk_id in chunk_ids:
            self.chunks.pop(chunk_id, None)
            self.vectors.pop(chunk_id, None)
        self.entities = [
            entity for entity in self.entities if entity.document_id not in document_ids
        ]
        self.relationships = [
            relationship
            for relationship in self.relationships
            if relationship.document_id not in document_ids
        ]

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
        entry = self._record_deletion(document, reason, deleted_at, purge_after)
        if document.deleted_at is None:
            document.deleted_at = entry.deleted_at
            document.purge_after = entry.purge_after
            document.deletion_reason = entry.reason
        return TombstoneDocumentResponse(
            document_id=document.id,
            workspace_id=document.workspace_id,
            deleted_at=document.deleted_at or entry.deleted_at,
            purge_after=document.purge_after or entry.purge_after,
            reason=document.deletion_reason or entry.reason,
        )

    async def run_retention(
        self,
        workspace_id: str,
        as_of: datetime,
        purge_grace_days: int,
    ) -> tuple[int, int]:
        tombstoned = 0
        for document in list(self.documents.values()):
            if (
                document.workspace_id == workspace_id
                and document.deleted_at is None
                and document.retention_until is not None
                and document.retention_until <= as_of
            ):
                entry = self._record_deletion(
                    document,
                    "retention_expired",
                    as_of,
                    as_of + timedelta(days=purge_grace_days),
                )
                document.deleted_at = entry.deleted_at
                document.purge_after = entry.purge_after
                document.deletion_reason = entry.reason
                tombstoned += 1

        purge_ids = {
            document.id
            for document in self.documents.values()
            if document.workspace_id == workspace_id
            and document.deleted_at is not None
            and document.purge_after is not None
            and document.purge_after <= as_of
        }
        self._purge_documents(purge_ids)
        return tombstoned, len(purge_ids)

    async def deletion_ledger(self, workspace_id: str) -> list[DeletionLedgerEntry]:
        return sorted(
            [
                entry
                for (entry_workspace, _), entry in self.deletion_events.items()
                if entry_workspace == workspace_id
            ],
            key=lambda item: (item.deleted_at, item.event_id),
        )

    async def replay_deletion_ledger(
        self,
        workspace_id: str,
        entries: list[DeletionLedgerEntry],
        as_of: datetime,
    ) -> tuple[int, int]:
        tombstoned = 0
        purge_ids: set[str] = set()
        for entry in entries:
            if entry.workspace_id != workspace_id:
                raise ValueError("deletion ledger workspace mismatch")
            self.deletion_events.setdefault((workspace_id, entry.document_id), entry)
            document = self.documents.get(entry.document_id)
            if document is None or document.workspace_id != workspace_id:
                continue
            if entry.purge_after <= as_of:
                purge_ids.add(document.id)
                continue
            if document.deleted_at is None or document.deleted_at > entry.deleted_at:
                document.deleted_at = entry.deleted_at
                document.purge_after = entry.purge_after
                document.deletion_reason = entry.reason
                tombstoned += 1
        self._purge_documents(purge_ids)
        return tombstoned, len(purge_ids)

    async def check_ready(self) -> None:
        return None

    async def close(self) -> None:
        return None


store = MemoryKnowledgeStore()
