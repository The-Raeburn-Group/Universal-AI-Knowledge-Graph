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
async def test_postgres_ingestion_search_and_workspace_isolation() -> None:
    database_url = os.environ.get("UKG_DATABASE_URL")
    if not database_url:
        pytest.skip("UKG_DATABASE_URL is required for PostgreSQL integration test")

    store = PostgresKnowledgeStore(database_url, embedding_dimensions=384)
    embedding_provider = LocalHashEmbeddingProvider(dimensions=384)
    ingestion = IngestionService(store, embedding_provider)
    search = SearchService(store, embedding_provider)
    workspace_a = f"postgres-a-{uuid4()}"
    workspace_b = f"postgres-b-{uuid4()}"

    try:
        await store.check_ready()
        document_a = await ingestion.ingest(
            DocumentIn(
                workspace_id=workspace_a,
                source="manual",
                title="Tenant A security note",
                body="Acme renewal requires a security review. Sarah owns procurement.",
                metadata={"tenant": "a"},
            )
        )
        await ingestion.ingest(
            DocumentIn(
                workspace_id=workspace_b,
                source="manual",
                title="Tenant B security note",
                body="Acme renewal also mentions security review in another workspace.",
                metadata={"tenant": "b"},
            )
        )

        response = await search.search(
            SearchRequest(
                workspace_id=workspace_a,
                query="Acme security review renewal",
                limit=10,
            )
        )

        assert response.hits
        assert response.hits[0].document_id == document_a.id
        assert all(hit.metadata.get("tenant") == "a" for hit in response.hits)
        assert all(hit.document_id == document_a.id for hit in response.hits)
    finally:
        await store.close()
