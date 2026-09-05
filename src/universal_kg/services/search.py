from __future__ import annotations

from universal_kg.domain import Entity, Relationship, SearchRequest, SearchResponse
from universal_kg.processing.embeddings import EmbeddingProvider, get_embedding_provider
from universal_kg.storage.base import KnowledgeStore
from universal_kg.storage.factory import get_knowledge_store


class SearchService:
    def __init__(
        self,
        knowledge_store: KnowledgeStore | None = None,
        embedding_provider: EmbeddingProvider | None = None,
    ) -> None:
        self.knowledge_store = knowledge_store or get_knowledge_store()
        self.embedding_provider = embedding_provider or get_embedding_provider()

    async def search(self, request: SearchRequest) -> SearchResponse:
        vector = (await self.embedding_provider.embed([request.query]))[0]
        hits = await self.knowledge_store.search(request.workspace_id, vector, request.limit)
        entities: list[Entity] = []
        relationships: list[Relationship] = []
        if request.include_graph:
            entities, relationships = await self.knowledge_store.graph_context(
                request.workspace_id, request.query
            )
        return SearchResponse(
            query=request.query,
            hits=hits,
            related_entities=entities,
            relationships=relationships,
        )
