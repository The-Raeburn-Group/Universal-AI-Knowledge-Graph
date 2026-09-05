from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

from pypdf import PdfReader

from universal_kg.connectors.base import Connector, registry
from universal_kg.domain import DocumentIn


class PdfConnector(Connector):
    async def load(self) -> AsyncIterator[DocumentIn]:
        file_path = Path(str(self.config.options["path"]))
        reader = PdfReader(str(file_path))
        text_pages = []
        for page in reader.pages:
            text_pages.append(page.extract_text() or "")
        yield DocumentIn(
            workspace_id=self.config.workspace_id,
            source="pdf",
            external_id=str(file_path),
            title=file_path.name,
            body="\n\n".join(text_pages),
            metadata={"path": str(file_path), "page_count": len(text_pages)},
        )


registry.register("pdf", PdfConnector)
