from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from universal_kg.connectors.base import Connector, registry
from universal_kg.domain import DocumentIn

_MIN_TEXT_CHARS = 20


def _page_status(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return "empty"
    if len(stripped) < _MIN_TEXT_CHARS:
        return "low_text"
    return "text"


def _build_pdf_body(pages: list[str]) -> tuple[str, list[dict[str, Any]]]:
    parts: list[str] = []
    spans: list[dict[str, Any]] = []
    offset = 0

    for page_number, text in enumerate(pages, start=1):
        if parts:
            separator = "\n\n"
            parts.append(separator)
            offset += len(separator)

        start = offset
        parts.append(text)
        offset += len(text)
        spans.append(
            {
                "page": page_number,
                "start": start,
                "end": offset,
                "char_count": len(text.strip()),
                "status": _page_status(text),
            }
        )

    return "".join(parts), spans


class PdfConnector(Connector):
    async def load(self) -> AsyncIterator[DocumentIn]:
        file_path = Path(str(self.config.options["path"]))
        reader = PdfReader(str(file_path))
        text_pages = [page.extract_text() or "" for page in reader.pages]
        body, page_spans = _build_pdf_body(text_pages)

        if not body.strip():
            pages = ",".join(str(span["page"]) for span in page_spans)
            raise ValueError(f"pdf_has_no_extractable_text:ocr_required_pages={pages}")

        empty_pages = [span["page"] for span in page_spans if span["status"] == "empty"]
        low_text_pages = [
            span["page"] for span in page_spans if span["status"] == "low_text"
        ]
        ocr_required_pages = sorted([*empty_pages, *low_text_pages])

        yield DocumentIn(
            workspace_id=self.config.workspace_id,
            source="pdf",
            external_id=str(file_path),
            title=file_path.name,
            body=body,
            metadata={
                "path": str(file_path),
                "page_count": len(text_pages),
                "text_page_count": len(text_pages) - len(empty_pages),
                "page_spans": page_spans,
                "empty_pages": empty_pages,
                "low_text_pages": low_text_pages,
                "ocr_required_pages": ocr_required_pages,
                "ocr_performed": False,
                "extraction_method": "pypdf",
            },
        )


registry.register("pdf", PdfConnector)
