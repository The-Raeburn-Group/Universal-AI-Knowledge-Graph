from __future__ import annotations

import csv
import io
import re
import shutil
import subprocess
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from pypdf import PdfReader

from universal_kg.connectors.base import Connector, registry
from universal_kg.domain import DocumentIn

_MIN_TEXT_CHARS = 20
_OCR_LANGUAGE_PATTERN = re.compile(r"[A-Za-z0-9_+.-]{1,64}")


@dataclass(frozen=True)
class _OcrResult:
    text: str
    mean_confidence: float | None
    word_count: int


def _page_status(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return "empty"
    if len(stripped) < _MIN_TEXT_CHARS:
        return "low_text"
    return "text"


def _bool_option(options: dict[str, Any], name: str, default: bool) -> bool:
    value = options.get(name, default)
    if not isinstance(value, bool):
        raise ValueError(f"invalid_pdf_option:{name}")
    return value


def _int_option(
    options: dict[str, Any],
    name: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    value = options.get(name, default)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"invalid_pdf_option:{name}")
    if value < minimum or value > maximum:
        raise ValueError(f"invalid_pdf_option:{name}")
    return value


def _ocr_language(options: dict[str, Any]) -> str:
    value = options.get("ocr_language", "eng")
    if not isinstance(value, str) or _OCR_LANGUAGE_PATTERN.fullmatch(value) is None:
        raise ValueError("invalid_pdf_option:ocr_language")
    return value


def _parse_tesseract_tsv(tsv: str) -> _OcrResult:
    lines: dict[tuple[str, str, str], list[str]] = {}
    confidences: list[float] = []
    reader = csv.DictReader(io.StringIO(tsv), delimiter="\t")
    for row in reader:
        token = (row.get("text") or "").strip()
        if not token:
            continue
        try:
            confidence = float(row.get("conf") or "-1")
        except ValueError:
            continue
        if confidence < 0:
            continue
        key = (
            row.get("block_num") or "0",
            row.get("par_num") or "0",
            row.get("line_num") or "0",
        )
        lines.setdefault(key, []).append(token)
        confidences.append(confidence)

    text = "\n".join(" ".join(tokens) for tokens in lines.values()).strip()
    mean_confidence = None
    if confidences:
        mean_confidence = round(sum(confidences) / len(confidences), 2)
    return _OcrResult(text=text, mean_confidence=mean_confidence, word_count=len(confidences))


def _resolve_ocr_binaries() -> tuple[str, str]:
    pdftoppm = shutil.which("pdftoppm")
    tesseract = shutil.which("tesseract")
    missing: list[str] = []
    if pdftoppm is None:
        missing.append("pdftoppm")
    if tesseract is None:
        missing.append("tesseract")
    if missing:
        raise ValueError(f"pdf_ocr_unavailable:missing={','.join(missing)}")
    if pdftoppm is None or tesseract is None:
        raise AssertionError("OCR binary validation invariant failed")
    return pdftoppm, tesseract


def _run_local_command(args: list[str], timeout_seconds: int, stage: str) -> str:
    try:
        completed = subprocess.run(  # noqa: S603 - fixed local binaries, shell=False.
            args,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError(f"pdf_ocr_timeout:stage={stage}") from exc
    if completed.returncode != 0:
        stderr = " ".join(completed.stderr.split())[:200]
        raise ValueError(f"pdf_ocr_failed:stage={stage}:detail={stderr}")
    return completed.stdout


def _ocr_pdf_pages(
    file_path: Path,
    page_numbers: list[int],
    language: str,
    dpi: int,
    timeout_seconds: int,
) -> dict[int, _OcrResult]:
    pdftoppm, tesseract = _resolve_ocr_binaries()
    results: dict[int, _OcrResult] = {}
    with TemporaryDirectory(prefix="ukg-pdf-ocr-") as temp_dir:
        temp_path = Path(temp_dir)
        for page_number in page_numbers:
            output_prefix = temp_path / f"page-{page_number}"
            _run_local_command(
                [
                    pdftoppm,
                    "-f",
                    str(page_number),
                    "-l",
                    str(page_number),
                    "-singlefile",
                    "-r",
                    str(dpi),
                    "-png",
                    str(file_path),
                    str(output_prefix),
                ],
                timeout_seconds,
                "render",
            )
            image_path = output_prefix.with_suffix(".png")
            tsv = _run_local_command(
                [
                    tesseract,
                    str(image_path),
                    "stdout",
                    "-l",
                    language,
                    "--psm",
                    "6",
                    "tsv",
                ],
                timeout_seconds,
                "recognise",
            )
            results[page_number] = _parse_tesseract_tsv(tsv)
    return results


def _should_use_ocr_text(existing: str, ocr_text: str) -> bool:
    existing_status = _page_status(existing)
    ocr_status = _page_status(ocr_text)
    if ocr_status == "empty":
        return False
    if existing_status == "empty":
        return True
    if existing_status == "low_text" and ocr_status == "text":
        return True
    return existing_status == "low_text" and len(ocr_text.strip()) > len(existing.strip())


def _build_pdf_body(
    pages: list[str],
    page_metadata: list[dict[str, Any]] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
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
        span: dict[str, Any] = {
            "page": page_number,
            "start": start,
            "end": offset,
            "char_count": len(text.strip()),
            "status": _page_status(text),
        }
        if page_metadata is not None and page_number <= len(page_metadata):
            span.update(page_metadata[page_number - 1])
        spans.append(span)

    return "".join(parts), spans


class PdfConnector(Connector):
    async def load(self) -> AsyncIterator[DocumentIn]:
        file_path = Path(str(self.config.options["path"]))
        reader = PdfReader(str(file_path))
        pypdf_pages = [page.extract_text() or "" for page in reader.pages]
        final_pages = list(pypdf_pages)
        candidate_pages = [
            page_number
            for page_number, text in enumerate(pypdf_pages, start=1)
            if _page_status(text) in {"empty", "low_text"}
        ]
        page_metadata: list[dict[str, Any]] = [
            {"extraction_method": "pypdf", "ocr_attempted": False}
            for _ in pypdf_pages
        ]

        ocr_enabled = _bool_option(self.config.options, "ocr_enabled", False)
        ocr_language = _ocr_language(self.config.options)
        ocr_dpi = _int_option(self.config.options, "ocr_dpi", 200, 72, 400)
        ocr_timeout_seconds = _int_option(
            self.config.options,
            "ocr_timeout_seconds",
            60,
            1,
            300,
        )
        ocr_max_pages = _int_option(self.config.options, "ocr_max_pages", 50, 1, 200)

        ocr_results: dict[int, _OcrResult] = {}
        ocr_pages: list[int] = []
        if ocr_enabled and candidate_pages:
            if len(candidate_pages) > ocr_max_pages:
                raise ValueError(
                    "pdf_ocr_page_limit_exceeded:"
                    f"candidates={len(candidate_pages)}:max={ocr_max_pages}"
                )
            ocr_results = _ocr_pdf_pages(
                file_path,
                candidate_pages,
                ocr_language,
                ocr_dpi,
                ocr_timeout_seconds,
            )
            for page_number in candidate_pages:
                result = ocr_results[page_number]
                page_meta = page_metadata[page_number - 1]
                page_meta["ocr_attempted"] = True
                page_meta["ocr_word_count"] = result.word_count
                if result.mean_confidence is not None:
                    page_meta["ocr_confidence"] = result.mean_confidence
                if _should_use_ocr_text(final_pages[page_number - 1], result.text):
                    final_pages[page_number - 1] = result.text
                    page_meta["extraction_method"] = "tesseract"
                    ocr_pages.append(page_number)

        body, page_spans = _build_pdf_body(final_pages, page_metadata)
        empty_pages = [span["page"] for span in page_spans if span["status"] == "empty"]
        low_text_pages = [
            span["page"] for span in page_spans if span["status"] == "low_text"
        ]
        ocr_required_pages = sorted([*empty_pages, *low_text_pages])

        if not body.strip():
            pages = ",".join(str(page) for page in ocr_required_pages)
            raise ValueError(f"pdf_has_no_extractable_text:ocr_required_pages={pages}")

        attempted_pages = candidate_pages if ocr_enabled else []
        failed_pages = sorted(set(attempted_pages) - set(ocr_pages))
        metadata: dict[str, Any] = {
            "path": str(file_path),
            "page_count": len(final_pages),
            "text_page_count": len(final_pages) - len(empty_pages),
            "page_spans": page_spans,
            "empty_pages": empty_pages,
            "low_text_pages": low_text_pages,
            "ocr_candidate_pages": candidate_pages,
            "ocr_required_pages": ocr_required_pages,
            "ocr_attempted_pages": attempted_pages,
            "ocr_pages": ocr_pages,
            "ocr_failed_pages": failed_pages,
            "ocr_performed": bool(attempted_pages),
            "extraction_method": "pypdf+tesseract" if attempted_pages else "pypdf",
        }
        if attempted_pages:
            metadata.update(
                {
                    "ocr_engine": "tesseract",
                    "ocr_execution": "local-process",
                    "ocr_data_egress": "none",
                    "ocr_language": ocr_language,
                    "ocr_dpi": ocr_dpi,
                }
            )
            weighted_confidence = sum(
                result.mean_confidence * result.word_count
                for result in ocr_results.values()
                if result.mean_confidence is not None and result.word_count > 0
            )
            confidence_words = sum(
                result.word_count
                for result in ocr_results.values()
                if result.mean_confidence is not None and result.word_count > 0
            )
            if confidence_words:
                metadata["ocr_mean_confidence"] = round(
                    weighted_confidence / confidence_words,
                    2,
                )

        yield DocumentIn(
            workspace_id=self.config.workspace_id,
            source="pdf",
            external_id=str(file_path),
            title=file_path.name,
            body=body,
            metadata=metadata,
        )


registry.register("pdf", PdfConnector)
