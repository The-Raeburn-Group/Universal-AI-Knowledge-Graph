from __future__ import annotations

import pytest

from universal_kg.domain import DocumentIn, SearchRequest
from universal_kg.services.ingestion import IngestionService
from universal_kg.services.search import SearchService


@pytest.mark.asyncio
async def test_ingestion_and_search_round_trip() -> None:
    document = DocumentIn(
        workspace_id="test",
        source="manual",
        title="Customer note",
        body="Acme Corp needs a security review before renewal. Sarah owns procurement.",
        metadata={"system": "crm"},
    )
    created = await IngestionService().ingest(document)
    assert created.title == "Customer note"

    response = await SearchService().search(
        SearchRequest(
            workspace_id="test",
            query="security review renewal",
            limit=3,
        )
    )
    assert response.hits
    assert response.hits[0].document_id == created.id
