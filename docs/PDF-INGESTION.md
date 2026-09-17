# PDF ingestion and page provenance

The PDF connector preserves page boundaries so retrieved evidence can be traced back to the source page rather than only to a whole document.

## Extraction contract

The connector first uses `pypdf` text extraction. For every source page it records a `page_spans` entry with:

- one-based page number;
- start/end offsets into the durable document body;
- extracted character count;
- extraction status: `text`, `low_text`, or `empty`;
- the page extraction method;
- OCR-attempt/confidence/word-count signals when OCR was attempted.

Document metadata also records:

- total page count;
- count of pages with any extracted text;
- empty and low-text page lists;
- initial `ocr_candidate_pages`;
- final unresolved `ocr_required_pages`;
- `ocr_attempted_pages`, `ocr_pages`, and `ocr_failed_pages`;
- whether OCR was performed;
- document extraction method (`pypdf` or `pypdf+tesseract`).

A PDF with no usable text after the configured extraction path fails visibly with `pdf_has_no_extractable_text` and the pages still requiring OCR. The connector does **not** invent text or silently treat an unreadable image-only PDF as successfully extracted.

## Local OCR

OCR is opt-in through connector option `ocr_enabled: true`. It is deliberately local-only: the connector renders selected pages with `pdftoppm` and recognises them with Tesseract. No page image or OCR text is sent to an external service by this implementation.

Defaults and hard bounds:

- `ocr_language`: `eng`; validated as a Tesseract language expression;
- `ocr_dpi`: `200`, allowed range 72–400;
- `ocr_timeout_seconds`: `60` per render/recognition command, allowed range 1–300;
- `ocr_max_pages`: `50`, allowed range 1–200.

Only pages initially classified as `empty` or `low_text` are OCR candidates. If candidate count exceeds `ocr_max_pages`, ingestion fails visibly rather than launching unbounded CPU work. If OCR is requested but `pdftoppm` or `tesseract` is unavailable, ingestion fails with `pdf_ocr_unavailable`.

Tesseract TSV output is used to retain word-level confidence. The connector records page confidence and word count and only replaces native extraction when OCR is materially better: empty native pages can accept non-empty OCR; low-text native pages prefer normal-text OCR or longer low-text OCR. Pages that remain empty/low-text stay in `ocr_required_pages`.

When OCR runs, document metadata records `ocr_engine: tesseract`, `ocr_execution: local-process`, and `ocr_data_egress: none`. These values describe this connector path only; they are not a statement about other services in the wider deployment.

## Page-aware chunking

When valid PDF page spans are present, chunks are built independently inside each page. A chunk cannot contain text from two PDF pages. Each chunk carries:

- `page_number`;
- `page_start` / `page_end`;
- total `page_count`;
- extraction status and extracted character count where available;
- page extraction method;
- page OCR attempted/confidence/word-count fields where available;
- source path and document extraction method;
- local OCR engine/execution/egress metadata when OCR was used.

This preserves page-level evidence even when a page is split into several overlapping chunks.

## Retrieval citations

Search promotes `page_start` and `page_end` from chunk metadata into `CitationProvenance`. Downstream answer generation can therefore render an exact page reference without re-parsing connector-specific metadata. Existing document/chunk IDs and SHA-256 content hashes remain part of the citation contract.

## Evidence and quality boundary

The CI integration test creates a genuinely image-only PDF, runs the same local Poppler/Tesseract path used by the connector, verifies recovered text, confidence metadata, page provenance and no-egress metadata, then verifies that those fields survive chunking.

This is evidence that the local OCR execution path works for the tested native image-PDF fixture. It is **not** evidence that OCR is authoritative or that all scanned documents are correctly understood. OCR quality varies with scan resolution, fonts, rotation, handwriting, tables and image quality. Confidence remains an evidence signal, not a truth score.

Still-open RAI-131 work includes:

1. mixed/rotated/degraded scanned-PDF benchmark sets and quality thresholds;
2. table and layout extraction;
3. diagrams and embedded-image understanding;
4. Office-document formats;
5. downstream UI rendering of page-level citations;
6. operational resource controls/observability for large OCR workloads.

## RAI-131 scope

This implementation adds bounded, local Tesseract OCR to the existing page-provenance pipeline and preserves OCR provenance through retrieval chunks. It materially advances scanned-PDF handling while keeping unresolved extraction visible. It does not yet complete full multimodal document understanding.
