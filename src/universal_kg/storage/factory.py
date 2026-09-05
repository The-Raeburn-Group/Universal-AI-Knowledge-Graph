from __future__ import annotations

from functools import lru_cache

from universal_kg.config import get_settings
from universal_kg.storage.base import KnowledgeStore
from universal_kg.storage.memory import store as memory_store
from universal_kg.storage.postgres import PostgresKnowledgeStore


@lru_cache(maxsize=1)
def get_knowledge_store() -> KnowledgeStore:
    settings = get_settings()
    if settings.storage_backend == "postgres":
        return PostgresKnowledgeStore(
            database_url=settings.database_url,
            embedding_dimensions=settings.embedding_dimensions,
        )
    return memory_store


def clear_knowledge_store_cache() -> None:
    get_knowledge_store.cache_clear()
