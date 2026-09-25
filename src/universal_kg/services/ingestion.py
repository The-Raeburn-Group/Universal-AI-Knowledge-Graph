from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256

from universal_kg.domain import Document, DocumentIn
from universal_kg.processing.chunking import chunk_document
from universal_kg.processing.embeddings import EmbeddingProvider, get_embedding_provider
from universal_kg.processing.extraction import extract_entities, extract_relationships
from universal_kg.storage.base import KnowledgeStore
from universal_kg.storage.factory import get_knowledge_store


class IngestionService:
    def __init__(
        self,
        knowledge_store: KnowledgeStore | None = None,
        embedding_provider: EmbeddingProvider | None = None,
    ) -> None:
        self.knowledge_store = knowledge_store or get_knowledge_store()
        self.embedding_provider = embedding_provider or get_embedding_provider()

    async def ingest(self, payload: DocumentIn) -> Document:
        created_at = datetime.now(UTC)
        retention_until = (
            created_at + timedelta(days=payload.retention_days)
            if payload.retention_days is not None
            else None
        )
        payload_data = payload.model_dump(exclude={"access", "retention_days", "metadata"})
        metadata = {
            **payload.metadata,
            "content_sha256": sha256(payload.body.encode("utf-8")).hexdigest(),
        }
        document = Document(
            **payload_data,
            metadata=metadata,
            access=payload.access,
            created_at=created_at,
            retention_until=retention_until,
        )
        chunks = chunk_document(document)
        vectors = (
            await self.embedding_provider.embed([chunk.text for chunk in chunks]) if chunks else []
        )
        entities = extract_entities(chunks)
        relationships = extract_relationships(chunks, entities)

        await self.knowledge_store.upsert_document(document)
        await self.knowledge_store.upsert_chunks(chunks, vectors)
        await self.knowledge_store.upsert_graph(entities, relationships)
        return document
