from __future__ import annotations

import os
from uuid import uuid4

import pytest

from universal_kg.domain import AccessContext, AccessPolicy, DocumentIn, SearchRequest
from universal_kg.processing.embeddings import LocalHashEmbeddingProvider
from universal_kg.services.ingestion import IngestionService
from universal_kg.services.lifecycle import LifecycleService
from universal_kg.services.search import SearchService
from universal_kg.storage.postgres import PostgresKnowledgeStore


@pytest.mark.asyncio
async def test_postgres_ingestion_survives_restart_and_enforces_workspace_and_acl() -> None:
    database_url = os.environ.get("UKG_DATABASE_URL")
    if not database_url:
        pytest.skip("UKG_DATABASE_URL is required for PostgreSQL integration test")

    embedding_provider = LocalHashEmbeddingProvider(dimensions=384)
    workspace_a = f"postgres-a-{uuid4()}"
    workspace_b = f"postgres-b-{uuid4()}"
    first_store = PostgresKnowledgeStore(database_url, embedding_dimensions=384)
    public_id = ""
    restricted_id = ""

    try:
        await first_store.check_ready()
        ingestion = IngestionService(first_store, embedding_provider)
        public_doc = await ingestion.ingest(
            DocumentIn(
                workspace_id=workspace_a,
                source="manual",
                title="Tenant A public security note",
                body="Acme Renewal requires Security Review. Sarah owns Procurement.",
                metadata={"tenant": "a", "classification": "workspace"},
            )
        )
        public_id = public_doc.id
        restricted_doc = await ingestion.ingest(
            DocumentIn(
                workspace_id=workspace_a,
                source="crm",
                title="Tenant A board confidential",
                body="Project Aurora Confidential requires Board Review.",
                metadata={"tenant": "a", "classification": "restricted"},
                access=AccessPolicy(
                    visibility="restricted",
                    groups=["board"],
                    source_acl_ref="crm-acl:aurora:v9",
                ),
            )
        )
        restricted_id = restricted_doc.id
        await ingestion.ingest(
            DocumentIn(
                workspace_id=workspace_b,
                source="manual",
                title="Tenant B security note",
                body="Acme Renewal also requires Security Review. Brian owns Procurement.",
                metadata={"tenant": "b"},
            )
        )
    finally:
        await first_store.close()

    reopened_store = PostgresKnowledgeStore(database_url, embedding_dimensions=384)
    try:
        await reopened_store.check_ready()
        search = SearchService(reopened_store, embedding_provider)

        outsider = AccessContext(
            workspace_id=workspace_a,
            principal_id="viewer@example.com",
            roles=["viewer"],
            groups=["staff"],
        )
        outsider_response = await search.search(
            SearchRequest(
                workspace_id=workspace_a,
                query="Acme Aurora security review renewal",
                limit=10,
            ),
            outsider,
        )
        outsider_ids = {hit.document_id for hit in outsider_response.hits}
        assert public_id in outsider_ids
        assert restricted_id not in outsider_ids
        assert all(hit.metadata.get("tenant") == "a" for hit in outsider_response.hits)
        assert all("Aurora" not in entity.name for entity in outsider_response.related_entities)

        board = AccessContext(
            workspace_id=workspace_a,
            principal_id="director@example.com",
            groups=["BOARD"],
        )
        board_response = await search.search(
            SearchRequest(
                workspace_id=workspace_a,
                query="Aurora Confidential Board Review",
                limit=10,
            ),
            board,
        )
        assert restricted_id in {hit.document_id for hit in board_response.hits}
        assert any("Aurora" in entity.name for entity in board_response.related_entities)

        entities, relationships = await reopened_store.graph_context(
            workspace_a,
            "Aurora Confidential",
            outsider,
        )
        assert all("Aurora" not in entity.name for entity in entities)
        assert all(
            "Aurora" not in relationship.subject and "Aurora" not in relationship.object
            for relationship in relationships
        )
    finally:
        await reopened_store.close()


@pytest.mark.asyncio
async def test_postgres_tombstone_hides_vector_and_graph_then_cascade_purges() -> None:
    database_url = os.environ.get("UKG_DATABASE_URL")
    if not database_url:
        pytest.skip("UKG_DATABASE_URL is required for PostgreSQL integration test")

    embeddings = LocalHashEmbeddingProvider(dimensions=384)
    workspace = f"postgres-lifecycle-{uuid4()}"
    store = PostgresKnowledgeStore(database_url, embedding_dimensions=384)
    try:
        await store.check_ready()
        ingestion = IngestionService(store, embeddings)
        search = SearchService(store, embeddings)
        lifecycle = LifecycleService(store, purge_grace_days=0)
        access = AccessContext(workspace_id=workspace, principal_id="admin@example.com")

        document = await ingestion.ingest(
            DocumentIn(
                workspace_id=workspace,
                source="manual",
                title="Postgres lifecycle source",
                body="Project DurableTombstone requires Security Review and Board Approval.",
            )
        )
        before = await search.search(
            SearchRequest(
                workspace_id=workspace,
                query="DurableTombstone Security Board",
                limit=10,
            ),
            access,
        )
        assert document.id in {hit.document_id for hit in before.hits}
        assert before.related_entities

        tombstone = await lifecycle.tombstone(workspace, document.id, "source_deleted")
        assert tombstone is not None

        hidden = await search.search(
            SearchRequest(
                workspace_id=workspace,
                query="DurableTombstone Security Board",
                limit=10,
            ),
            access,
        )
        assert document.id not in {hit.document_id for hit in hidden.hits}
        assert hidden.related_entities == []
        assert hidden.relationships == []

        retention = await lifecycle.run_retention(workspace, as_of=tombstone.purge_after)
        assert retention.tombstoned == 0
        assert retention.purged == 1
        assert await lifecycle.tombstone(workspace, document.id, "already_purged") is None
    finally:
        await store.close()
