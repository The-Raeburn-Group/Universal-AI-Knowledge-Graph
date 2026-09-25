from __future__ import annotations

import os
from uuid import uuid4

import pytest

from universal_kg.domain import (
    AccessContext,
    AccessPolicy,
    Chunk,
    Document,
    DocumentIn,
    Entity,
    Relationship,
    SearchRequest,
)
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
        await first_store.upsert_graph(
            [
                Entity(
                    workspace_id=workspace_a,
                    document_id=restricted_id,
                    name="ParentAclDriftSentinel",
                    access=AccessPolicy(visibility="workspace"),
                )
            ],
            [
                Relationship(
                    workspace_id=workspace_a,
                    document_id=restricted_id,
                    subject="ParentAclDriftSentinel",
                    predicate="reveals",
                    object="RestrictedGraphDetail",
                    access=AccessPolicy(visibility="workspace"),
                )
            ],
        )
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

        drift_entities, drift_relationships = await reopened_store.graph_context(
            workspace_a,
            "ParentAclDriftSentinel RestrictedGraphDetail",
            outsider,
        )
        assert all(entity.name != "ParentAclDriftSentinel" for entity in drift_entities)
        assert all(
            relationship.subject != "ParentAclDriftSentinel"
            for relationship in drift_relationships
        )

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
async def test_postgres_acl_filter_preserves_accessible_top_k_beyond_hnsw_candidates() -> None:
    database_url = os.environ.get("UKG_DATABASE_URL")
    if not database_url:
        pytest.skip("UKG_DATABASE_URL is required for PostgreSQL integration test")

    workspace = f"postgres-acl-topk-{uuid4()}"
    restricted_policy = AccessPolicy(
        visibility="restricted",
        groups=["board"],
        source_acl_ref="crm-acl:nearest-restricted:v1",
    )
    store = PostgresKnowledgeStore(database_url, embedding_dimensions=384)
    query_vector = [1.0] + [0.0] * 383
    restricted_vector = query_vector
    accessible_vector = [0.999, 0.0447101778] + [0.0] * 382
    restricted_doc = Document(
        id=str(uuid4()),
        workspace_id=workspace,
        source="crm",
        title="Restricted nearest candidates",
        body="Nearest vectors are inaccessible to the viewer.",
        access=restricted_policy,
    )
    accessible_doc = Document(
        id=str(uuid4()),
        workspace_id=workspace,
        source="manual",
        title="Accessible result",
        body="This permitted result must survive selective ACL filtering.",
        access=AccessPolicy(visibility="workspace"),
    )

    try:
        await store.check_ready()
        await store.upsert_document(restricted_doc)
        await store.upsert_document(accessible_doc)

        restricted_chunks = [
            Chunk(
                id=str(uuid4()),
                document_id=restricted_doc.id,
                workspace_id=workspace,
                text=f"Restricted neighbour {index}",
                ordinal=index,
                access=restricted_policy,
            )
            for index in range(64)
        ]
        accessible_chunk = Chunk(
            id=str(uuid4()),
            document_id=accessible_doc.id,
            workspace_id=workspace,
            text="Accessible result beyond the nearest restricted neighbours.",
            ordinal=0,
            access=AccessPolicy(visibility="workspace"),
        )
        await store.upsert_chunks(
            [*restricted_chunks, accessible_chunk],
            [*[restricted_vector for _ in restricted_chunks], accessible_vector],
        )

        outsider = AccessContext(
            workspace_id=workspace,
            principal_id="viewer@example.com",
            groups=["staff"],
        )
        hits = await store.search(
            workspace,
            query_vector,
            limit=1,
            access=outsider,
        )

        assert [hit.document_id for hit in hits] == [accessible_doc.id]
    finally:
        await store.close()

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

@pytest.mark.asyncio
async def test_postgres_lexical_search_and_multihop_graph_remain_acl_safe() -> None:
    database_url = os.environ.get("UKG_DATABASE_URL")
    if not database_url:
        pytest.skip("UKG_DATABASE_URL is required for PostgreSQL integration test")

    workspace = f"postgres-hybrid-{uuid4()}"
    store = PostgresKnowledgeStore(database_url, embedding_dimensions=384)
    public_policy = AccessPolicy(visibility="workspace")
    restricted_policy = AccessPolicy(
        visibility="restricted",
        groups=["board"],
        source_acl_ref="crm:hybrid-secret:v1",
    )
    public_doc = Document(
        id=str(uuid4()),
        workspace_id=workspace,
        source="manual",
        title="Public LexicalNeedle",
        body="LexicalNeedle public retrieval evidence.",
        access=public_policy,
    )
    restricted_doc = Document(
        id=str(uuid4()),
        workspace_id=workspace,
        source="crm",
        title="Restricted LexicalNeedle",
        body="LexicalNeedle restricted retrieval evidence.",
        access=restricted_policy,
    )
    graph_doc = Document(
        id=str(uuid4()),
        workspace_id=workspace,
        source="manual",
        title="Graph traversal source",
        body="GraphStart GraphMiddle GraphEnd.",
        access=public_policy,
    )
    restricted_graph_doc = Document(
        id=str(uuid4()),
        workspace_id=workspace,
        source="crm",
        title="Restricted graph source",
        body="GraphMiddle RestrictedGraphSecret.",
        access=restricted_policy,
    )
    zero_vector = [0.0] * 384

    try:
        await store.check_ready()
        for document in [public_doc, restricted_doc, graph_doc, restricted_graph_doc]:
            await store.upsert_document(document)

        await store.upsert_chunks(
            [
                Chunk(
                    id=str(uuid4()),
                    document_id=public_doc.id,
                    workspace_id=workspace,
                    text="LexicalNeedle public retrieval evidence.",
                    ordinal=0,
                    access=public_policy,
                ),
                Chunk(
                    id=str(uuid4()),
                    document_id=restricted_doc.id,
                    workspace_id=workspace,
                    text="LexicalNeedle restricted retrieval evidence.",
                    ordinal=0,
                    access=restricted_policy,
                ),
            ],
            [zero_vector, zero_vector],
        )

        outsider = AccessContext(
            workspace_id=workspace,
            principal_id="viewer@example.com",
            groups=["staff"],
        )
        lexical_hits = await store.lexical_search(
            workspace,
            "LexicalNeedle",
            limit=10,
            access=outsider,
        )
        assert [hit.document_id for hit in lexical_hits] == [public_doc.id]

        board = AccessContext(
            workspace_id=workspace,
            principal_id="director@example.com",
            groups=["BOARD"],
        )
        board_hits = await store.lexical_search(
            workspace,
            "LexicalNeedle",
            limit=10,
            access=board,
        )
        assert {hit.document_id for hit in board_hits} == {
            public_doc.id,
            restricted_doc.id,
        }

        await store.upsert_graph(
            [
                Entity(
                    id=str(uuid4()),
                    workspace_id=workspace,
                    document_id=graph_doc.id,
                    name="GraphStart",
                    access=public_policy,
                ),
                Entity(
                    id=str(uuid4()),
                    workspace_id=workspace,
                    document_id=graph_doc.id,
                    name="GraphMiddle",
                    access=public_policy,
                ),
                Entity(
                    id=str(uuid4()),
                    workspace_id=workspace,
                    document_id=graph_doc.id,
                    name="GraphEnd",
                    access=public_policy,
                ),
                Entity(
                    id=str(uuid4()),
                    workspace_id=workspace,
                    document_id=restricted_graph_doc.id,
                    name="RestrictedGraphSecret",
                    access=restricted_policy,
                ),
            ],
            [
                Relationship(
                    id=str(uuid4()),
                    workspace_id=workspace,
                    document_id=graph_doc.id,
                    subject="GraphStart",
                    predicate="links_to",
                    object="GraphMiddle",
                    confidence=0.9,
                    access=public_policy,
                ),
                Relationship(
                    id=str(uuid4()),
                    workspace_id=workspace,
                    document_id=graph_doc.id,
                    subject="GraphMiddle",
                    predicate="links_to",
                    object="GraphEnd",
                    confidence=0.9,
                    access=public_policy,
                ),
                Relationship(
                    id=str(uuid4()),
                    workspace_id=workspace,
                    document_id=restricted_graph_doc.id,
                    subject="GraphMiddle",
                    predicate="links_to",
                    object="RestrictedGraphSecret",
                    confidence=0.9,
                    access=restricted_policy,
                ),
            ],
        )

        entities, relationships = await store.graph_context(
            workspace,
            "GraphStart",
            outsider,
            depth=2,
        )
        assert {entity.name for entity in entities} == {
            "GraphStart",
            "GraphMiddle",
            "GraphEnd",
        }
        assert all(entity.name != "RestrictedGraphSecret" for entity in entities)
        assert all(
            relationship.object != "RestrictedGraphSecret"
            for relationship in relationships
        )
    finally:
        await store.close()

