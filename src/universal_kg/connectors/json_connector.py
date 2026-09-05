from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

from universal_kg.connectors.base import Connector, registry
from universal_kg.domain import DocumentIn


class JsonFileConnector(Connector):
    async def load(self) -> AsyncIterator[DocumentIn]:
        path = Path(str(self.config.options["path"]))
        records = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(records, dict):
            records = [records]
        for index, record in enumerate(records):
            title = str(
                record.get("title") or record.get("name") or f"JSON record {index + 1}"
            )
            body = json.dumps(record, ensure_ascii=False, indent=2)
            yield DocumentIn(
                workspace_id=self.config.workspace_id,
                source="json",
                external_id=str(record.get("id", index)),
                title=title,
                body=body,
                metadata={"path": str(path)},
            )


registry.register("json", JsonFileConnector)
