from __future__ import annotations

import asyncio

import typer

from universal_kg.connectors import json_connector, pdf_connector  # noqa: F401
from universal_kg.connectors.base import ConnectorConfig, registry
from universal_kg.services.ingestion import IngestionService

app = typer.Typer(help="Universal AI Knowledge Graph command line tools.")


@app.command()
def ingest_file(workspace_id: str, connector: str, file_path: str) -> None:
    async def run_job() -> None:
        cfg = ConnectorConfig(
            workspace_id=workspace_id,
            source_name=connector,
            options={"path": file_path},
        )
        instance = registry.create(connector, cfg)
        service = IngestionService()
        count = 0
        async for document in instance.load():
            await service.ingest(document)
            count += 1
        typer.echo(f"Ingested {count} document(s)")

    asyncio.run(run_job())
