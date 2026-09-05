from __future__ import annotations

from universal_kg.config import get_settings
from universal_kg.domain import Chunk, Document


def chunk_document(document: Document) -> list[Chunk]:
    settings = get_settings()
    text = " ".join(document.body.split())
    if not text:
        return []

    max_chars = settings.max_chunk_chars
    overlap = min(settings.chunk_overlap_chars, max_chars // 3)
    chunks: list[Chunk] = []
    start = 0
    ordinal = 0

    while start < len(text):
        end = min(start + max_chars, len(text))
        chunk_text = text[start:end].strip()
        if chunk_text:
            chunks.append(
                Chunk(
                    document_id=document.id,
                    workspace_id=document.workspace_id,
                    text=chunk_text,
                    ordinal=ordinal,
                    metadata={"title": document.title, "source": document.source},
                )
            )
            ordinal += 1
        if end >= len(text):
            break
        start = max(0, end - overlap)

    return chunks
