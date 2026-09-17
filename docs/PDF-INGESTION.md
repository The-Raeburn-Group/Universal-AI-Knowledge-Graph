# PDF ingestion and page provenance

The PDF connector preserves page boundaries so retrieved evidence can be traced back to the source page rather than only to a whole document.

## Extraction contract

The connector currently uses `pypdf` text extraction. For every source page it records a `page_spans` entry with:

- one-based page number;
- start/end offsets into the durable document body;
- extracted character count;
- extraction status: `text`, `low_text`, or `empty`.

Document metadata also records:

- total page count;
- count of pages with any extracted text;
- empty and low-text page lists;
- `ocr_required_pages`;
- `ocr_performed: false`;
- extraction method (`pypdf`).

A PDF with no extractable text fails visibly with `pdf_has_no_extractable_text` and the pages requiring OCR. The connector does **not** invent text or silently treat an image-only PDF as successfully extracted.

## Page-aware chunking

When valid PDF page spans are present, chunks are built independently inside each page. A chunk cannot contain text from two PDF pages. Each chunk carries:

- `page_number`;
- `page_start` / `page_end`;
- total `page_count`;
- extraction status and extracted character count where available;
- source path and extraction method.

This preserves page-level evidence even when a page is split into several overlapping chunks.

## Retrieval citations

Search promotes `page_start` and `page_end` from chunk metadata into `CitationProvenance`. Downstream answer generation can therefore render an exact page reference without re-parsing connector-specific metadata. Existing document/chunk IDs and SHA-256 content hashes remain part of the citation contract.

## OCR boundary

This slice detects pages that likely require OCR; it does not perform OCR. A page is marked for OCR when extracted text is empty or contains fewer than 20 non-whitespace characters.

Future OCR work must:

1. use a reviewed OCR provider or local engine with explicit data-residency policy;
2. retain the original page number and extraction method;
3. distinguish native text from OCR-derived text;
4. record OCR confidence/quality signals rather than presenting OCR as authoritative;
5. preserve page-level provenance through chunking and retrieval;
6. test tables, diagrams, rotated pages and mixed native/scanned documents separately.

Until that work lands, `ocr_required_pages` is an operational diagnostic, not a claim that those pages were understood.

## RAI-131 scope

This closes the page-provenance and extraction-diagnostics gap in the existing PDF path. It does not yet complete RAI-131: OCR execution, table extraction, images/diagrams, office-document formats, OCR quality evaluation and citation rendering in downstream answer UIs remain open.
