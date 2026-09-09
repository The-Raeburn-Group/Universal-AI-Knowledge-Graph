from __future__ import annotations

from datetime import datetime
from typing import Protocol

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


class KnowledgeStore(Protocol):
    async def upsert_document(self, document: Document) -> None: ...

    async def upsert_chunks(self, chunks: list[Chunk], vectors: list[list[float]]) -> None: ...

    async def upsert_graph(
        self, entities: list[Entity], relationships: list[Relationship]
    ) -> None: ...

    async def search(
        self,
        workspace_id: str,
        query_vector: list[float],
        limit: int,
        access: AccessContext,
    ) -> list[SearchHit]: ...

    async def graph_context(
        self,
        workspace_id: str,
        query: str,
        access: AccessContext,
    ) -> tuple[list[Entity], list[Relationship]]: ...

    async def tombstone_document(
        self,
        workspace_id: str,
        document_id: str,
        reason: str,
        deleted_at: datetime,
        purge_after: datetime,
    ) -> TombstoneDocumentResponse | None: ...

    async def run_retention(
        self,
        workspace_id: str,
        as_of: datetime,
        purge_grace_days: int,
    ) -> tuple[int, int]: ...

    async def deletion_ledger(self, workspace_id: str) -> list[DeletionLedgerEntry]: ...

    async def replay_deletion_ledger(
        self,
        workspace_id: str,
        entries: list[DeletionLedgerEntry],
        as_of: datetime,
    ) -> tuple[int, int]: ...

    async def check_ready(self) -> None: ...

    async def close(self) -> None: ...
