from __future__ import annotations

import json
import math

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from universal_kg.config import Settings
from universal_kg.connectors import json_connector  # noqa: F401
from universal_kg.connectors.base import ConnectorConfig, ConnectorRegistry, registry
from universal_kg.processing.embeddings import LocalHashEmbeddingProvider, get_embedding_provider
from universal_kg.security import InMemoryRateLimiter, require_human_approval
from universal_kg.storage.factory import clear_knowledge_store_cache, get_knowledge_store
from universal_kg.storage.memory import store as memory_store
from universal_kg.storage.postgres import PostgresKnowledgeStore


@pytest.mark.asyncio
async def test_json_connector_loads_records_with_workspace_provenance(tmp_path) -> None:
    payload_path = tmp_path / "records.json"
    payload_path.write_text(
        json.dumps(
            [
                {"id": "crm-1", "title": "Renewal", "value": "security review"},
                {"id": "crm-2", "name": "Procurement", "owner": "Sarah"},
            ]
        ),
        encoding="utf-8",
    )
    connector = registry.create(
        "json",
        ConnectorConfig(
            workspace_id="tenant-a",
            source_name="json",
            options={"path": str(payload_path)},
        ),
    )

    documents = [document async for document in connector.load()]

    assert [document.external_id for document in documents] == ["crm-1", "crm-2"]
    assert [document.title for document in documents] == ["Renewal", "Procurement"]
    assert all(document.workspace_id == "tenant-a" for document in documents)
    assert all(document.metadata["path"] == str(payload_path) for document in documents)


def test_connector_registry_rejects_unknown_connector() -> None:
    connector_registry = ConnectorRegistry()
    config = ConnectorConfig(workspace_id="tenant-a", source_name="missing")

    with pytest.raises(KeyError, match="Unknown connector: missing"):
        connector_registry.create("missing", config)


def test_production_requires_persistent_postgres_storage() -> None:
    with pytest.raises(ValidationError, match="production requires UKG_STORAGE_BACKEND=postgres"):
        Settings(environment="production", storage_backend="memory")

    settings = Settings(environment="production", storage_backend="postgres")
    assert settings.storage_backend == "postgres"


@pytest.mark.asyncio
async def test_local_embeddings_are_deterministic_normalized_and_dimensioned() -> None:
    provider = LocalHashEmbeddingProvider(dimensions=32)

    first, second = await provider.embed(["Raeburn security review", "Raeburn security review"])

    assert first == second
    assert len(first) == 32
    assert math.isclose(math.sqrt(sum(value * value for value in first)), 1.0)


def test_embedding_factory_uses_configured_local_dimensions() -> None:
    provider = get_embedding_provider(
        Settings(embedding_provider="local-hash", embedding_dimensions=64)
    )
    assert isinstance(provider, LocalHashEmbeddingProvider)
    assert provider.dimensions == 64


def test_security_controls_enforce_rate_limit_and_human_approval() -> None:
    limiter = InMemoryRateLimiter(max_requests=1, window_seconds=60)
    limiter.check("tenant-a:actor-1")

    with pytest.raises(HTTPException) as rate_error:
        limiter.check("tenant-a:actor-1")
    assert rate_error.value.status_code == 429

    require_human_approval(True, "knowledge.delete")
    with pytest.raises(HTTPException) as approval_error:
        require_human_approval(False, "knowledge.delete")
    assert approval_error.value.status_code == 403


def test_storage_factory_defaults_to_memory_and_cache_can_be_cleared() -> None:
    clear_knowledge_store_cache()
    assert get_knowledge_store() is memory_store
    clear_knowledge_store_cache()


def test_postgres_store_rejects_schema_dimension_drift_before_connecting() -> None:
    with pytest.raises(ValueError, match="Changing vector dimensions requires a versioned database migration"):
        PostgresKnowledgeStore(
            "postgresql+psycopg://ukg:ukg@localhost:5432/ukg",
            embedding_dimensions=768,
        )
