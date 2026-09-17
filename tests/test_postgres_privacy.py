from __future__ import annotations

import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from universal_kg.privacy import PostgresPrivacyRepository, WorkspacePrivacyService

_WORKSPACES = ("privacy-test-a", "privacy-test-b")
_DELETE_FIXTURE_SQL = (
    "delete from relationships where workspace_id in (:workspace_a, :workspace_b)",
    "delete from entities where workspace_id in (:workspace_a, :workspace_b)",
    "delete from chunks where workspace_id in (:workspace_a, :workspace_b)",
    "delete from documents where workspace_id in (:workspace_a, :workspace_b)",
)


async def _clear_fixture(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            params = {"workspace_a": _WORKSPACES[0], "workspace_b": _WORKSPACES[1]}
            for statement in _DELETE_FIXTURE_SQL:
                await connection.execute(text(statement), params)
    finally:
        await engine.dispose()


async def _seed_workspace(database_url: str, workspace_id: str, suffix: str) -> None:
    engine = create_async_engine(database_url)
    vector = "[" + ",".join(["0.01"] * 384) + "]"
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    insert into documents (id, workspace_id, source, external_id, title, body)
                    values (
                        :document_id, :workspace_id, 'privacy-test',
                        :external_id, :title, :body
                    )
                    """
                ),
                {
                    "document_id": f"privacy-doc-{suffix}",
                    "workspace_id": workspace_id,
                    "external_id": f"subject-{suffix}",
                    "title": f"Subject {suffix}",
                    "body": f"Personal data fixture {suffix}",
                },
            )
            await connection.execute(
                text(
                    """
                    insert into chunks (id, document_id, workspace_id, text, ordinal, embedding)
                    values (
                        :chunk_id, :document_id, :workspace_id,
                        :text, 0, cast(:embedding as vector)
                    )
                    """
                ),
                {
                    "chunk_id": f"privacy-chunk-{suffix}",
                    "document_id": f"privacy-doc-{suffix}",
                    "workspace_id": workspace_id,
                    "text": f"Chunk fixture {suffix}",
                    "embedding": vector,
                },
            )
            await connection.execute(
                text(
                    """
                    insert into entities (id, workspace_id, document_id, name, type)
                    values (:entity_id, :workspace_id, :document_id, :name, 'person')
                    """
                ),
                {
                    "entity_id": f"privacy-entity-{suffix}",
                    "workspace_id": workspace_id,
                    "document_id": f"privacy-doc-{suffix}",
                    "name": f"Person {suffix}",
                },
            )
            await connection.execute(
                text(
                    """
                    insert into relationships (
                        id, workspace_id, document_id, subject, predicate, object,
                        evidence_chunk_id, confidence
                    ) values (
                        :relationship_id, :workspace_id, :document_id, :subject,
                        'mentioned_in', :object, :chunk_id, 1.0
                    )
                    """
                ),
                {
                    "relationship_id": f"privacy-relationship-{suffix}",
                    "workspace_id": workspace_id,
                    "document_id": f"privacy-doc-{suffix}",
                    "subject": f"Person {suffix}",
                    "object": f"Document {suffix}",
                    "chunk_id": f"privacy-chunk-{suffix}",
                },
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_postgres_workspace_export_and_erasure_preserve_other_tenant() -> None:
    database_url = os.environ.get("UKG_DATABASE_URL")
    if not database_url:
        pytest.skip("UKG_DATABASE_URL is required for PostgreSQL privacy integration")

    await _clear_fixture(database_url)
    await _seed_workspace(database_url, _WORKSPACES[0], "a")
    await _seed_workspace(database_url, _WORKSPACES[1], "b")

    repository = PostgresPrivacyRepository(database_url)
    service = WorkspacePrivacyService(repository)
    try:
        exported_a = await service.export(_WORKSPACES[0])
        assert exported_a["counts"] == {
            "documents": 1,
            "chunks": 1,
            "entities": 1,
            "relationships": 1,
        }
        assert exported_a["records"]["documents"][0]["body"] == "Personal data fixture a"
        assert exported_a["records"]["chunks"][0]["embedding"].startswith("[")

        receipt = await service.erase(_WORKSPACES[0])
        assert receipt.deleted == {
            "documents": 1,
            "chunks": 1,
            "entities": 1,
            "relationships": 1,
        }
        assert receipt.verified_remaining == {
            "documents": 0,
            "chunks": 0,
            "entities": 0,
            "relationships": 0,
        }

        exported_b = await service.export(_WORKSPACES[1])
        assert exported_b["counts"] == {
            "documents": 1,
            "chunks": 1,
            "entities": 1,
            "relationships": 1,
        }
        assert exported_b["records"]["documents"][0]["body"] == "Personal data fixture b"
    finally:
        await service.close()
        await _clear_fixture(database_url)
