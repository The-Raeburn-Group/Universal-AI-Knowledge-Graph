from __future__ import annotations

import math
from dataclasses import dataclass, field

from universal_kg.domain import Chunk, Document, Entity, Relationship, SearchHit


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

    async def search(
        self, workspace_id: str, query_vector: list[float], limit: int
    ) -> list[SearchHit]:
        scored: list[tuple[float, Chunk]] = []
        for chunk_id, chunk in self.chunks.items():
            if chunk.workspace_id != workspace_id:
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
        self, workspace_id: str, query: str
    ) -> tuple[list[Entity], list[Relationship]]:
        tokens = {token.lower() for token in query.split() if len(token) > 2}
        entities = [
            entity
            for entity in self.entities
            if entity.workspace_id == workspace_id
            and any(token in entity.name.lower() for token in tokens)
        ][:20]
        names = {entity.name for entity in entities}
        relationships = [
            rel
            for rel in self.relationships
            if rel.workspace_id == workspace_id
            and (rel.subject in names or rel.object in names)
        ][:50]
        return entities, relationships

    async def check_ready(self) -> None:
        return None

    async def close(self) -> None:
        return None


store = MemoryKnowledgeStore()
