from __future__ import annotations

from typing import Protocol

from universal_kg.domain import Chunk, Document, Entity, Relationship, SearchHit


class KnowledgeStore(Protocol):
    async def upsert_document(self, document: Document) -> None: ...

    async def upsert_chunks(self, chunks: list[Chunk], vectors: list[list[float]]) -> None: ...

    async def upsert_graph(
        self, entities: list[Entity], relationships: list[Relationship]
    ) -> None: ...

    async def search(
        self, workspace_id: str, query_vector: list[float], limit: int
    ) -> list[SearchHit]: ...

    async def graph_context(
        self, workspace_id: str, query: str
    ) -> tuple[list[Entity], list[Relationship]]: ...

    async def check_ready(self) -> None: ...

    async def close(self) -> None: ...
