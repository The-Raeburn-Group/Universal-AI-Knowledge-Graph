from __future__ import annotations

import os
from uuid import uuid4

import pytest

from universal_kg.domain import DocumentIn, SearchRequest
from universal_kg.processing.embeddings import LocalHashEmbeddingProvider
from universal_kg.services.ingestion import IngestionService
from universal_kg.services.search import SearchService
from universal_kg.storage.postgres import PostgresKnowledgeStore


@pytest.mark.asyncio
async def test_postgres_ingestion_survives_restart_and_isolates_workspaces() -> None:
    database_url = os.environ.get("UKG_DATABASE_URL")
    if not database_url:
        pytest.skip("UKG_DATABASE_URL is required for PostgreSQL integration test")

    embedding_provider = LocalHashEmbeddingProvider(dimensions=384)
    workspace_a = f"postgres-a-{uuid4()}"
    workspace_b = f"postgres-b-{uuid4()}"
    first_store = PostgresKnowledgeStore(database_url, embedding_dimensions=384)
    document_a_id = ""

    try:
        await first_store.check_ready()
        ingestion = IngestionService(first_store, embedding_provider)
        document_a = await ingestion.ingest(
            DocumentIn(
                workspace_id=workspace_a,
                source="manual",
                title="Tenant A security note",
                body="Acme Renewal requires Security Review. Sarah owns Procurement.",
                metadata={"tenant": "a"},
            )
        )
        document_a_id = document_a.id
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
        response = await search.search(
            SearchRequest(
                workspace_id=workspace_a,
                query="Acme security review renewal",
                limit=10,
            )
        )

        assert response.hits
        assert response.hits[0].document_id == document_a_id
        assert all(hit.metadata.get("tenant") == "a" for hit in response.hits)
        assert all(hit.document_id == document_a_id for hit in response.hits)

        entities, relationships = await reopened_store.graph_context(workspace_a, "Acme Renewal")
        assert entities
        assert all(entity.workspace_id == workspace_a for entity in entities)
        assert all(relationship.workspace_id == workspace_a for relationship in relationships)
    finally:
        await reopened_store.close()
