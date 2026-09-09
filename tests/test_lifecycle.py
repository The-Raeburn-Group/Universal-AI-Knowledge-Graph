from __future__ import annotations

from datetime import timedelta

import pytest

from universal_kg.domain import AccessContext, DocumentIn, SearchRequest
from universal_kg.processing.embeddings import LocalHashEmbeddingProvider
from universal_kg.services.ingestion import IngestionService
from universal_kg.services.lifecycle import LifecycleService
from universal_kg.services.search import SearchService
from universal_kg.storage.memory import MemoryKnowledgeStore


@pytest.mark.asyncio
async def test_manual_tombstone_immediately_hides_vector_and_graph_then_purges() -> None:
    store = MemoryKnowledgeStore()
    embeddings = LocalHashEmbeddingProvider(dimensions=384)
    ingestion = IngestionService(store, embeddings)
    search = SearchService(store, embeddings)
    lifecycle = LifecycleService(store, purge_grace_days=0)
    workspace = "lifecycle-manual"
    access = AccessContext(workspace_id=workspace, principal_id="admin@example.com")

    document = await ingestion.ingest(
        DocumentIn(
            workspace_id=workspace,
            source="manual",
            title="Orion archive policy",
            body="Project Orion Archive Policy requires Security Review.",
        )
    )
    before = await search.search(
        SearchRequest(workspace_id=workspace, query="Orion Archive Security Review", limit=10),
        access,
    )
    assert document.id in {hit.document_id for hit in before.hits}
    assert any("Orion" in entity.name for entity in before.related_entities)

    tombstone = await lifecycle.tombstone(
        workspace,
        document.id,
        "source_deleted",
        now=document.created_at + timedelta(hours=1),
    )
    assert tombstone is not None
    assert tombstone.reason == "source_deleted"

    after = await search.search(
        SearchRequest(workspace_id=workspace, query="Orion Archive Security Review", limit=10),
        access,
    )
    assert document.id not in {hit.document_id for hit in after.hits}
    assert all("Orion" not in entity.name for entity in after.related_entities)
    assert all(
        "Orion" not in relationship.subject and "Orion" not in relationship.object
        for relationship in after.relationships
    )

    repeated = await lifecycle.tombstone(
        workspace,
        document.id,
        "different_reason_must_not_overwrite",
        now=tombstone.deleted_at + timedelta(hours=1),
    )
    assert repeated is not None
    assert repeated.deleted_at == tombstone.deleted_at
    assert repeated.purge_after == tombstone.purge_after
    assert repeated.reason == "source_deleted"

    retention = await lifecycle.run_retention(workspace, as_of=tombstone.purge_after)
    assert retention.tombstoned == 0
    assert retention.purged == 1
    assert document.id not in store.documents
    assert await lifecycle.tombstone(workspace, document.id, "already_purged") is None


@pytest.mark.asyncio
async def test_retention_expiry_tombstones_before_physical_purge() -> None:
    store = MemoryKnowledgeStore()
    embeddings = LocalHashEmbeddingProvider(dimensions=384)
    ingestion = IngestionService(store, embeddings)
    search = SearchService(store, embeddings)
    lifecycle = LifecycleService(store, purge_grace_days=7)
    workspace = "lifecycle-retention"
    access = AccessContext(workspace_id=workspace, principal_id="admin@example.com")

    document = await ingestion.ingest(
        DocumentIn(
            workspace_id=workspace,
            source="manual",
            title="Temporary knowledge",
            body="Project RetentionMarker is temporary source material.",
            retention_days=1,
        )
    )
    assert document.retention_until is not None

    expiry_run = await lifecycle.run_retention(
        workspace,
        as_of=document.retention_until + timedelta(seconds=1),
    )
    assert expiry_run.tombstoned == 1
    assert expiry_run.purged == 0

    hidden = await search.search(
        SearchRequest(workspace_id=workspace, query="RetentionMarker", limit=10),
        access,
    )
    assert hidden.hits == []
    assert hidden.related_entities == []

    tombstoned = store.documents[document.id]
    assert tombstoned.deleted_at is not None
    assert tombstoned.purge_after is not None
    assert tombstoned.deletion_reason == "retention_expired"

    purge_run = await lifecycle.run_retention(
        workspace,
        as_of=tombstoned.purge_after,
    )
    assert purge_run.tombstoned == 0
    assert purge_run.purged == 1
    assert document.id not in store.documents


@pytest.mark.asyncio
async def test_lifecycle_rejects_naive_as_of_timestamp() -> None:
    from datetime import datetime

    lifecycle = LifecycleService(MemoryKnowledgeStore(), purge_grace_days=1)
    with pytest.raises(ValueError, match="timezone"):
        await lifecycle.run_retention("lifecycle-naive", as_of=datetime(2026, 9, 9))
