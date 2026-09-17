from __future__ import annotations

from typing import Any

from universal_kg.config import get_settings
from universal_kg.domain import Chunk, Document


def _chunk_text(text: str, max_chars: int, overlap: int) -> list[str]:
    normalised = " ".join(text.split())
    if not normalised:
        return []

    parts: list[str] = []
    start = 0
    while start < len(normalised):
        end = min(start + max_chars, len(normalised))
        part = normalised[start:end].strip()
        if part:
            parts.append(part)
        if end >= len(normalised):
            break
        start = max(0, end - overlap)
    return parts


def _pdf_page_spans(document: Document) -> list[dict[str, Any]]:
    raw_spans = document.metadata.get("page_spans")
    if not isinstance(raw_spans, list):
        return []

    spans: list[dict[str, Any]] = []
    for raw_span in raw_spans:
        if not isinstance(raw_span, dict):
            return []
        page = raw_span.get("page")
        start = raw_span.get("start")
        end = raw_span.get("end")
        if not all(isinstance(value, int) and not isinstance(value, bool) for value in (page, start, end)):
            return []
        if page < 1 or start < 0 or end < start or end > len(document.body):
            return []
        spans.append(raw_span)
    return spans


def _base_metadata(document: Document) -> dict[str, Any]:
    metadata: dict[str, Any] = {"title": document.title, "source": document.source}
    source_path = document.metadata.get("path")
    if isinstance(source_path, str) and source_path.strip():
        metadata["source_path"] = source_path
    extraction_method = document.metadata.get("extraction_method")
    if isinstance(extraction_method, str) and extraction_method.strip():
        metadata["extraction_method"] = extraction_method
    page_count = document.metadata.get("page_count")
    if isinstance(page_count, int) and not isinstance(page_count, bool) and page_count >= 1:
        metadata["page_count"] = page_count
    return metadata


def _chunk_pdf_document(document: Document, max_chars: int, overlap: int) -> list[Chunk]:
    spans = _pdf_page_spans(document)
    if not spans:
        return []

    chunks: list[Chunk] = []
    ordinal = 0
    base_metadata = _base_metadata(document)
    for span in spans:
        page_number = int(span["page"])
        start = int(span["start"])
        end = int(span["end"])
        page_text = document.body[start:end]
        page_metadata = {
            **base_metadata,
            "page_number": page_number,
            "page_start": page_number,
            "page_end": page_number,
        }
        status = span.get("status")
        if isinstance(status, str):
            page_metadata["page_extraction_status"] = status
        char_count = span.get("char_count")
        if isinstance(char_count, int) and not isinstance(char_count, bool):
            page_metadata["page_char_count"] = char_count

        for chunk_text in _chunk_text(page_text, max_chars, overlap):
            chunks.append(
                Chunk(
                    document_id=document.id,
                    workspace_id=document.workspace_id,
                    text=chunk_text,
                    ordinal=ordinal,
                    metadata=page_metadata,
                    access=document.access,
                )
            )
            ordinal += 1
    return chunks


def chunk_document(document: Document) -> list[Chunk]:
    settings = get_settings()
    max_chars = settings.max_chunk_chars
    overlap = min(settings.chunk_overlap_chars, max_chars // 3)

    if document.source == "pdf" and document.metadata.get("page_spans") is not None:
        page_chunks = _chunk_pdf_document(document, max_chars, overlap)
        if page_chunks:
            return page_chunks

    chunks: list[Chunk] = []
    for ordinal, chunk_text in enumerate(_chunk_text(document.body, max_chars, overlap)):
        chunks.append(
            Chunk(
                document_id=document.id,
                workspace_id=document.workspace_id,
                text=chunk_text,
                ordinal=ordinal,
                metadata=_base_metadata(document),
                access=document.access,
            )
        )
    return chunks
