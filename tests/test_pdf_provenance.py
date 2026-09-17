from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont

from universal_kg.connectors.base import ConnectorConfig
from universal_kg.connectors.pdf_connector import (
    PdfConnector,
    _build_pdf_body,
    _parse_tesseract_tsv,
)
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
async def test_pdf_connector_records_page_spans_and_ocr_diagnostics(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _FakeReader.pages = [
        _FakePage("First page has enough extracted text for retrieval and citation."),
        _FakePage(""),
        _FakePage("short"),
        _FakePage("Fourth page also has enough extracted text for normal retrieval."),
    ]
    monkeypatch.setattr("universal_kg.connectors.pdf_connector.PdfReader", _FakeReader)
    pdf_path = tmp_path / "example.pdf"
    connector = PdfConnector(
        ConnectorConfig(
            workspace_id="workspace-a",
            source_name="pdf",
            options={"path": str(pdf_path)},
        )
    )

    documents = [document async for document in connector.load()]
    assert len(documents) == 1
    document = documents[0]
    assert document.metadata["page_count"] == 4
    assert document.metadata["empty_pages"] == [2]
    assert document.metadata["low_text_pages"] == [3]
    assert document.metadata["ocr_candidate_pages"] == [2, 3]
    assert document.metadata["ocr_required_pages"] == [2, 3]
    assert document.metadata["ocr_attempted_pages"] == []
    assert document.metadata["ocr_performed"] is False
    assert document.metadata["extraction_method"] == "pypdf"

    spans = document.metadata["page_spans"]
    assert [span["page"] for span in spans] == [1, 2, 3, 4]
    assert document.body[spans[0]["start"] : spans[0]["end"]].startswith("First page")
    assert document.body[spans[3]["start"] : spans[3]["end"]].startswith("Fourth page")


@pytest.mark.asyncio
async def test_pdf_connector_fails_visibly_when_all_pages_require_ocr(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _FakeReader.pages = [_FakePage(""), _FakePage(None)]
    monkeypatch.setattr("universal_kg.connectors.pdf_connector.PdfReader", _FakeReader)
    pdf_path = tmp_path / "scanned.pdf"
    connector = PdfConnector(
        ConnectorConfig(
            workspace_id="workspace-a",
            source_name="pdf",
            options={"path": str(pdf_path)},
        )
    )

    with pytest.raises(
        ValueError,
        match=r"pdf_has_no_extractable_text:ocr_required_pages=1,2",
    ):
        _ = [document async for document in connector.load()]


def test_tesseract_tsv_parser_preserves_lines_and_confidence() -> None:
    tsv = (
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
        "5\t1\t1\t1\t1\t1\t0\t0\t10\t10\t90.0\tRaeburn\n"
        "5\t1\t1\t1\t1\t2\t0\t0\t10\t10\t80.0\tAI\n"
        "5\t1\t1\t1\t2\t1\t0\t0\t10\t10\t70.0\tEvidence\n"
    )
    result = _parse_tesseract_tsv(tsv)
    assert result.text == "Raeburn AI\nEvidence"
    assert result.word_count == 3
    assert result.mean_confidence == 80.0


@pytest.mark.asyncio
@pytest.mark.skipif(
    shutil.which("tesseract") is None or shutil.which("pdftoppm") is None,
    reason="local OCR binaries are not installed",
)
async def test_local_ocr_extracts_image_only_pdf_and_retains_provenance(tmp_path: Path) -> None:
    font_path = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    if not font_path.exists():
        pytest.skip("DejaVu Sans test font is not installed")

    image = Image.new("RGB", (1800, 600), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(str(font_path), 72)
    draw.text((80, 220), "RAEBURN OCR TEST 12345", fill="black", font=font)
    pdf_path = tmp_path / "image-only.pdf"
    image.save(pdf_path, "PDF", resolution=150.0)

    connector = PdfConnector(
        ConnectorConfig(
            workspace_id="workspace-a",
            source_name="pdf",
            options={
                "path": str(pdf_path),
                "ocr_enabled": True,
                "ocr_dpi": 200,
                "ocr_timeout_seconds": 60,
            },
        )
    )
    documents = [document async for document in connector.load()]
    assert len(documents) == 1
    document = documents[0]

    assert "RAEBURN" in document.body.upper()
    assert document.metadata["ocr_candidate_pages"] == [1]
    assert document.metadata["ocr_attempted_pages"] == [1]
    assert document.metadata["ocr_pages"] == [1]
    assert document.metadata["ocr_failed_pages"] == []
    assert document.metadata["ocr_required_pages"] == []
    assert document.metadata["ocr_performed"] is True
    assert document.metadata["ocr_engine"] == "tesseract"
    assert document.metadata["ocr_execution"] == "local-process"
    assert document.metadata["ocr_data_egress"] == "none"
    assert document.metadata["extraction_method"] == "pypdf+tesseract"
    assert 0.0 <= document.metadata["ocr_mean_confidence"] <= 100.0

    span = document.metadata["page_spans"][0]
    assert span["extraction_method"] == "tesseract"
    assert span["ocr_attempted"] is True
    assert 0.0 <= span["ocr_confidence"] <= 100.0
    assert span["ocr_word_count"] >= 3

    persisted = Document(
        workspace_id=document.workspace_id,
        source=document.source,
        external_id=document.external_id,
        title=document.title,
        body=document.body,
        metadata=document.metadata,
    )
    chunks = chunk_document(persisted)
    assert chunks
    assert chunks[0].metadata["page_extraction_method"] == "tesseract"
    assert chunks[0].metadata["page_ocr_attempted"] is True
    assert chunks[0].metadata["page_ocr_word_count"] >= 3
    assert 0.0 <= chunks[0].metadata["page_ocr_confidence"] <= 100.0
    assert chunks[0].metadata["ocr_data_egress"] == "none"


def test_pdf_chunks_never_cross_page_boundaries_and_retain_page_metadata() -> None:
    page_one = "Page one evidence sentence. " * 10
    page_two = "Page two different evidence sentence. " * 10
    body, spans = _build_pdf_body([page_one, page_two])
    document = Document(
        workspace_id="workspace-a",
        source="pdf",
        external_id="fixtures/example.pdf",
        title="example.pdf",
        body=body,
        metadata={
            "path": "fixtures/example.pdf",
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
