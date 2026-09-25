from __future__ import annotations

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
from universal_kg.services.ingestion import IngestionService
from universal_kg.services.search import SearchService
from universal_kg.storage.memory import MemoryKnowledgeStore


class FixedEmbeddingProvider:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]


def access(workspace_id: str, *, groups: list[str] | None = None) -> AccessContext:
    return AccessContext(
        workspace_id=workspace_id,
        principal_id="viewer@example.com",
        groups=groups or [],
    )


async def add_chunk(
    store: MemoryKnowledgeStore,
    *,
    workspace: str,
    document_id: str,
    chunk_id: str,
    title: str,
    text: str,
    vector: list[float],
    policy: AccessPolicy | None = None,
) -> None:
    access_policy = policy or AccessPolicy(visibility="workspace")
    await store.upsert_document(
        Document(
            id=document_id,
            workspace_id=workspace,
            source="manual",
            title=title,
            body=text,
            access=access_policy,
        )
    )
    await store.upsert_chunks(
        [
            Chunk(
                id=chunk_id,
                document_id=document_id,
                workspace_id=workspace,
                text=text,
                ordinal=0,
                access=access_policy,
            )
        ],
        [vector],
    )


async def test_hybrid_fusion_recovers_exact_lexical_match_when_vector_prefers_other_chunk() -> None:
    store = MemoryKnowledgeStore()
    workspace = "hybrid-fusion"
    await add_chunk(
        store,
        workspace=workspace,
        document_id="vector-doc",
        chunk_id="vector-chunk",
        title="Semantically nearby",
        text="General procurement guidance and renewal planning.",
        vector=[1.0, 0.0],
    )
    await add_chunk(
        store,
        workspace=workspace,
        document_id="exact-doc",
        chunk_id="exact-chunk",
        title="Authoritative ExactNeedle policy",
        text="ExactNeedle is the authoritative control identifier for this workflow.",
        vector=[0.0, 1.0],
    )

    service = SearchService(store, FixedEmbeddingProvider())
    vector_only = await service.search(
        SearchRequest(
            workspace_id=workspace,
            query="ExactNeedle",
            retrieval_mode="vector",
            limit=1,
            rerank=False,
        ),
        access(workspace),
    )
    assert vector_only.hits[0].document_id == "vector-doc"

    hybrid = await service.search(
        SearchRequest(
            workspace_id=workspace,
            query="ExactNeedle",
            retrieval_mode="hybrid",
            limit=1,
        ),
        access(workspace),
    )
    assert hybrid.hits[0].document_id == "exact-doc"
    assert hybrid.hits[0].ranking is not None
    assert hybrid.hits[0].ranking.lexical_score is not None
    assert hybrid.diagnostics is not None
    assert hybrid.diagnostics.vector_candidates == 2
    assert hybrid.diagnostics.lexical_candidates == 1


async def test_lexical_retrieval_filters_acl_before_ranking() -> None:
    store = MemoryKnowledgeStore()
    workspace = "lexical-acl"
    restricted = AccessPolicy(
        visibility="restricted",
        groups=["board"],
        source_acl_ref="crm:secret:v1",
    )
    await add_chunk(
        store,
        workspace=workspace,
        document_id="restricted",
        chunk_id="restricted-chunk",
        title="Secret ExactNeedle",
        text="ExactNeedle secret board-only value.",
        vector=[1.0, 0.0],
        policy=restricted,
    )
    await add_chunk(
        store,
        workspace=workspace,
        document_id="public",
        chunk_id="public-chunk",
        title="Public ExactNeedle",
        text="ExactNeedle public reference.",
        vector=[0.0, 1.0],
    )

    service = SearchService(store, FixedEmbeddingProvider())
    outsider = await service.search(
        SearchRequest(
            workspace_id=workspace,
            query="ExactNeedle",
            retrieval_mode="lexical",
            limit=10,
        ),
        access(workspace),
    )
    assert [hit.document_id for hit in outsider.hits] == ["public"]

    board = await service.search(
        SearchRequest(
            workspace_id=workspace,
            query="ExactNeedle",
            retrieval_mode="lexical",
            limit=10,
        ),
        access(workspace, groups=["BOARD"]),
    )
    assert {hit.document_id for hit in board.hits} == {"public", "restricted"}


async def test_graph_traversal_is_depth_bounded_and_surfaces_conflicts_for_review() -> None:
    store = MemoryKnowledgeStore()
    workspace = "graph-depth"
    document = Document(
        id="graph-doc",
        workspace_id=workspace,
        source="manual",
        title="Graph evidence",
        body="Alpha Beta Gamma policy evidence.",
    )
    await store.upsert_document(document)
    entities = [
        Entity(
            id="entity-alpha",
            workspace_id=workspace,
            document_id=document.id,
            name="Alpha",
        ),
        Entity(
            id="entity-beta",
            workspace_id=workspace,
            document_id=document.id,
            name="Beta",
        ),
        Entity(
            id="entity-gamma",
            workspace_id=workspace,
            document_id=document.id,
            name="Gamma",
        ),
        Entity(
            id="entity-policy",
            workspace_id=workspace,
            document_id=document.id,
            name="Policy",
        ),
    ]
    relationships = [
        Relationship(
            id="rel-alpha-beta",
            workspace_id=workspace,
            document_id=document.id,
            subject="Alpha",
            predicate="links_to",
            object="Beta",
            confidence=0.9,
        ),
        Relationship(
            id="rel-beta-gamma",
            workspace_id=workspace,
            document_id=document.id,
            subject="Beta",
            predicate="links_to",
            object="Gamma",
            confidence=0.9,
        ),
        Relationship(
            id="rel-policy-50",
            workspace_id=workspace,
            document_id=document.id,
            subject="Policy",
            predicate="approved_limit",
            object="50",
            confidence=0.9,
        ),
        Relationship(
            id="rel-policy-60",
            workspace_id=workspace,
            document_id=document.id,
            subject="Policy",
            predicate="approved_limit",
            object="60",
            confidence=0.9,
        ),
    ]
    await store.upsert_graph(entities, relationships)

    depth_one_entities, depth_one_relationships = await store.graph_context(
        workspace, "Alpha", access(workspace), depth=1
    )
    assert {entity.name for entity in depth_one_entities} == {"Alpha", "Beta"}
    assert {relationship.id for relationship in depth_one_relationships} == {"rel-alpha-beta"}

    depth_two_entities, depth_two_relationships = await store.graph_context(
        workspace, "Alpha", access(workspace), depth=2
    )
    assert {entity.name for entity in depth_two_entities} == {"Alpha", "Beta", "Gamma"}
    assert {relationship.id for relationship in depth_two_relationships} == {
        "rel-alpha-beta",
        "rel-beta-gamma",
    }

    service = SearchService(store, FixedEmbeddingProvider())
    conflict_response = await service.search(
        SearchRequest(
            workspace_id=workspace,
            query="Policy",
            retrieval_mode="lexical",
            include_graph=True,
            graph_depth=1,
        ),
        access(workspace),
    )
    assert conflict_response.diagnostics is not None
    assert len(conflict_response.diagnostics.conflicts) == 1
    conflict = conflict_response.diagnostics.conflicts[0]
    assert conflict.subject == "Policy"
    assert conflict.predicate == "approved_limit"
    assert conflict.objects == ["50", "60"]
    assert conflict.resolution == "review_required"




async def test_lexical_backend_does_not_index_title_only_terms() -> None:
    store = MemoryKnowledgeStore()
    workspace = "lexical-field-parity"
    await add_chunk(
        store,
        workspace=workspace,
        document_id="title-only",
        chunk_id="title-only:0",
        title="TitleOnlyNeedle",
        text="The body intentionally omits the query token.",
        vector=[1.0, 0.0],
    )

    hits = await store.lexical_search(
        workspace,
        "TitleOnlyNeedle",
        limit=10,
        access=access(workspace),
    )
    assert hits == []


async def test_reranker_exact_bonus_uses_token_boundaries() -> None:
    store = MemoryKnowledgeStore()
    workspace = "rerank-boundary"
    await add_chunk(
        store,
        workspace=workspace,
        document_id="quarterly",
        chunk_id="quarterly:0",
        title="Quarterly report",
        text="General planning note.",
        vector=[1.0, 0.0],
    )

    response = await SearchService(store, FixedEmbeddingProvider()).search(
        SearchRequest(
            workspace_id=workspace,
            query="art",
            retrieval_mode="vector",
            limit=1,
        ),
        access(workspace),
    )
    assert response.hits[0].ranking is not None
    assert response.hits[0].ranking.rerank_score == 0.0


async def test_graph_seed_cap_prevents_omitted_seed_edges_from_entering_context() -> None:
    store = MemoryKnowledgeStore()
    workspace = "graph-seed-cap"
    document = Document(
        id="seed-doc",
        workspace_id=workspace,
        source="manual",
        title="Seed graph",
        body="Seed graph evidence.",
    )
    await store.upsert_document(document)

    entities = [
        Entity(
            id=f"entity-{index:02d}",
            workspace_id=workspace,
            document_id=document.id,
            name=f"SeedMatch {index:02d}",
        )
        for index in range(25)
    ]
    relationships = [
        Relationship(
            id="omitted-seed-edge",
            workspace_id=workspace,
            document_id=document.id,
            subject="SeedMatch 24",
            predicate="links_to",
            object="ShouldNotAppear",
            confidence=0.9,
        )
    ]
    await store.upsert_graph(entities, relationships)

    returned_entities, returned_relationships = await store.graph_context(
        workspace,
        "SeedMatch",
        access(workspace),
        depth=1,
    )
    assert len(returned_entities) == 20
    assert all(entity.name != "SeedMatch 24" for entity in returned_entities)
    assert all(
        relationship.id != "omitted-seed-edge"
        for relationship in returned_relationships
    )




async def test_document_duplicate_diagnostics_use_server_generated_body_fingerprints() -> None:
    store = MemoryKnowledgeStore()
    workspace = "document-duplicates"
    body = "Canonical duplicate document body with DocumentFingerprintNeedle."
    ingestion = IngestionService(store, FixedEmbeddingProvider())
    first = await ingestion.ingest(
        DocumentIn(
            workspace_id=workspace,
            source="manual",
            title="First copy",
            body=body,
        )
    )
    second = await ingestion.ingest(
        DocumentIn(
            workspace_id=workspace,
            source="manual",
            title="Second copy",
            body=body,
        )
    )

    response = await SearchService(store, FixedEmbeddingProvider()).search(
        SearchRequest(
            workspace_id=workspace,
            query="DocumentFingerprintNeedle",
            retrieval_mode="hybrid",
            limit=10,
        ),
        access(workspace),
    )
    assert response.diagnostics is not None
    assert len(response.diagnostics.document_duplicates) == 1
    duplicate = response.diagnostics.document_duplicates[0]
    assert duplicate.document_ids == sorted([first.id, second.id])


async def test_duplicate_diagnostics_group_exact_content_across_documents() -> None:
    store = MemoryKnowledgeStore()
    workspace = "duplicates"
    duplicate_text = "Exact duplicate policy content with FingerprintNeedle."
    await add_chunk(
        store,
        workspace=workspace,
        document_id="doc-a",
        chunk_id="chunk-a",
        title="Copy A",
        text=duplicate_text,
        vector=[1.0, 0.0],
    )
    await add_chunk(
        store,
        workspace=workspace,
        document_id="doc-b",
        chunk_id="chunk-b",
        title="Copy B",
        text=duplicate_text,
        vector=[0.9, 0.1],
    )

    response = await SearchService(store, FixedEmbeddingProvider()).search(
        SearchRequest(
            workspace_id=workspace,
            query="FingerprintNeedle",
            retrieval_mode="hybrid",
            limit=10,
        ),
        access(workspace),
    )
    assert response.diagnostics is not None
    assert len(response.diagnostics.duplicates) == 1
    duplicate = response.diagnostics.duplicates[0]
    assert duplicate.document_ids == ["doc-a", "doc-b"]
    assert duplicate.chunk_ids == ["chunk-a", "chunk-b"]
    assert response.diagnostics.document_duplicates == []
