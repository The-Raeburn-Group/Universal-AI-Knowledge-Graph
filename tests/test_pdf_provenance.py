from __future__ import annotations

from datetime import UTC, datetime

import pytest

from universal_kg.connectors.base import ConnectorConfig
from universal_kg.connectors.pdf_connector import PdfConnector, _build_pdf_body
from universal_kg.domain import Document, SearchHit
from universal_kg.processing.chunking import chunk_document
from universal_kg.services.search import _citation


class _FakePage:
    def __init__(self, text: str | None) -> None:
        self.text = text

    def extract_text(self) -> str | None:
        return self.text


class _FakeReader:
    pages: list[_FakePage] = []

    def __init__(self, _path: str) -> None:
        self.pages = list(type(self).pages)


@pytest.mark.asyncio
async def test_pdf_connector_records_page_spans_and_ocr_diagnostics(monkeypatch) -> None:
    _FakeReader.pages = [
        _FakePage("First page has enough extracted text for retrieval and citation."),
        _FakePage(""),
        _FakePage("short"),
        _FakePage("Fourth page also has enough extracted text for normal retrieval."),
    ]
    monkeypatch.setattr("universal_kg.connectors.pdf_connector.PdfReader", _FakeReader)
    connector = PdfConnector(
        ConnectorConfig(
            workspace_id="workspace-a",
            source_name="pdf",
            options={"path": "/tmp/example.pdf"},
        )
    )

    documents = [document async for document in connector.load()]
    assert len(documents) == 1
    document = documents[0]
    assert document.metadata["page_count"] == 4
    assert document.metadata["empty_pages"] == [2]
    assert document.metadata["low_text_pages"] == [3]
    assert document.metadata["ocr_required_pages"] == [2, 3]
    assert document.metadata["ocr_performed"] is False
    assert document.metadata["extraction_method"] == "pypdf"

    spans = document.metadata["page_spans"]
    assert [span["page"] for span in spans] == [1, 2, 3, 4]
    assert document.body[spans[0]["start"] : spans[0]["end"]].startswith("First page")
    assert document.body[spans[3]["start"] : spans[3]["end"]].startswith("Fourth page")


@pytest.mark.asyncio
async def test_pdf_connector_fails_visibly_when_all_pages_require_ocr(monkeypatch) -> None:
    _FakeReader.pages = [_FakePage(""), _FakePage(None)]
    monkeypatch.setattr("universal_kg.connectors.pdf_connector.PdfReader", _FakeReader)
    connector = PdfConnector(
        ConnectorConfig(
            workspace_id="workspace-a",
            source_name="pdf",
            options={"path": "/tmp/scanned.pdf"},
        )
    )

    with pytest.raises(
        ValueError,
        match=r"pdf_has_no_extractable_text:ocr_required_pages=1,2",
    ):
        _ = [document async for document in connector.load()]


def test_pdf_chunks_never_cross_page_boundaries_and_retain_page_metadata() -> None:
    page_one = "Page one evidence sentence. " * 10
    page_two = "Page two different evidence sentence. " * 10
    body, spans = _build_pdf_body([page_one, page_two])
    document = Document(
        workspace_id="workspace-a",
        source="pdf",
        external_id="/tmp/example.pdf",
        title="example.pdf",
        body=body,
        metadata={
            "path": "/tmp/example.pdf",
            "page_count": 2,
            "page_spans": spans,
            "extraction_method": "pypdf",
        },
    )

    chunks = chunk_document(document)
    assert len(chunks) == 2
    assert chunks[0].metadata["page_number"] == 1
    assert chunks[0].metadata["page_start"] == 1
    assert chunks[0].metadata["page_end"] == 1
    assert chunks[1].metadata["page_number"] == 2
    assert chunks[1].metadata["page_start"] == 2
    assert chunks[1].metadata["page_end"] == 2
    assert "Page two" not in chunks[0].text
    assert "Page one" not in chunks[1].text


def test_pdf_page_range_is_promoted_to_citation_provenance() -> None:
    hit = SearchHit(
        document_id="doc-1",
        chunk_id="chunk-1",
        title="example.pdf",
        text="Evidence from page seven.",
        score=0.9,
        source="pdf",
        metadata={"page_start": 7, "page_end": 7},
    )

    citation = _citation(hit, "workspace-a", datetime(2026, 9, 17, tzinfo=UTC))
    assert citation.page_start == 7
    assert citation.page_end == 7
    assert len(citation.content_sha256) == 64


def test_pdf_body_spans_point_to_exact_original_page_text() -> None:
    body, spans = _build_pdf_body(["one\nline", "two\nlines"])
    assert body[spans[0]["start"] : spans[0]["end"]] == "one\nline"
    assert body[spans[1]["start"] : spans[1]["end"]] == "two\nlines"
