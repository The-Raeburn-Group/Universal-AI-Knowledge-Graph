from __future__ import annotations

import hashlib
import math
from abc import ABC, abstractmethod

from universal_kg.config import Settings, get_settings


class EmbeddingProvider(ABC):
    dimensions: int

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError


class LocalHashEmbeddingProvider(EmbeddingProvider):
    """Deterministic local embedding fallback for tests and private/offline deployments.

    This is not intended to outperform commercial embedding models. It gives the
    platform a no-key development path and keeps the provider interface stable.
    """

    def __init__(self, dimensions: int = 384) -> None:
        self.dimensions = dimensions

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for token in text.lower().split():
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            idx = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[idx] += sign
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]


class OpenAIEmbeddingProvider(EmbeddingProvider):
    def __init__(
        self,
        api_key: str,
        dimensions: int = 384,
        model: str = "text-embedding-3-small",
    ) -> None:
        self.api_key = api_key
        self.dimensions = dimensions
        self.model = model

    async def embed(self, texts: list[str]) -> list[list[float]]:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=self.api_key)
        response = await client.embeddings.create(
            model=self.model,
            input=texts,
            dimensions=self.dimensions,
        )
        vectors = [item.embedding for item in response.data]
        if any(len(vector) != self.dimensions for vector in vectors):
            raise RuntimeError("embedding_provider_dimension_mismatch")
        return vectors


def get_embedding_provider(settings: Settings | None = None) -> EmbeddingProvider:
    settings = settings or get_settings()
    if settings.embedding_provider == "openai" and settings.openai_api_key:
        return OpenAIEmbeddingProvider(
            api_key=settings.openai_api_key,
            dimensions=settings.embedding_dimensions,
        )
    return LocalHashEmbeddingProvider(dimensions=settings.embedding_dimensions)
