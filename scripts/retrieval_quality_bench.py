from __future__ import annotations

import asyncio
import json

from universal_kg.domain import AccessContext, Chunk, Document, SearchRequest
from universal_kg.services.search import SearchService
from universal_kg.storage.memory import MemoryKnowledgeStore


class FixedEmbeddingProvider:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]


async def add(
    store: MemoryKnowledgeStore,
    *,
    document_id: str,
    chunk_id: str,
    title: str,
    text: str,
    vector: list[float],
) -> None:
    workspace = "retrieval-benchmark"
    await store.upsert_document(
        Document(
            id=document_id,
            workspace_id=workspace,
            source="manual",
            title=title,
            body=text,
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
            )
        ],
        [vector],
    )


async def main() -> int:
    store = MemoryKnowledgeStore()
    await add(
        store,
        document_id="semantic-neighbour",
        chunk_id="semantic-neighbour:0",
        title="Procurement renewal guidance",
        text="General procurement guidance and renewal planning.",
        vector=[1.0, 0.0],
    )
    await add(
        store,
        document_id="authoritative-exact",
        chunk_id="authoritative-exact:0",
        title="Control ExactNeedle",
        text="ExactNeedle is the authoritative control identifier.",
        vector=[0.0, 1.0],
    )

    access = AccessContext(
        workspace_id="retrieval-benchmark",
        principal_id="benchmark@example.com",
    )
    service = SearchService(store, FixedEmbeddingProvider())

    vector = await service.search(
        SearchRequest(
            workspace_id="retrieval-benchmark",
            query="ExactNeedle",
            retrieval_mode="vector",
            limit=1,
            rerank=False,
            include_graph=False,
        ),
        access,
    )
    hybrid = await service.search(
        SearchRequest(
            workspace_id="retrieval-benchmark",
            query="ExactNeedle",
            retrieval_mode="hybrid",
            limit=1,
            include_graph=False,
        ),
        access,
    )

    vector_exact_at_1 = int(vector.hits[0].document_id == "authoritative-exact")
    hybrid_exact_at_1 = int(hybrid.hits[0].document_id == "authoritative-exact")
    result = {
        "contract": "raeburnai.retrieval-quality.v1",
        "vector_exact_at_1": vector_exact_at_1,
        "hybrid_exact_at_1": hybrid_exact_at_1,
        "hybrid_improvement": hybrid_exact_at_1 - vector_exact_at_1,
        "hybrid_diagnostics": (
            hybrid.diagnostics.model_dump(mode="json")
            if hybrid.diagnostics is not None
            else None
        ),
    }
    print(json.dumps(result, sort_keys=True))

    if hybrid_exact_at_1 != 1:
        return 1
    if hybrid_exact_at_1 < vector_exact_at_1:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
