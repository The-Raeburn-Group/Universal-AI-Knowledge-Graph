from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, Protocol

import orjson
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from universal_kg.config import get_settings

PRIVACY_EXPORT_SCHEMA = "ukg.workspace-export.v1"
_PRIVACY_TABLES = ("documents", "chunks", "entities", "relationships")


class PrivacyVerificationError(RuntimeError):
    """Raised when an erasure operation cannot prove that scoped data is gone."""


@dataclass(frozen=True)
class WorkspaceErasureReceipt:
    workspace_id: str
    erased_at: datetime
    deleted: dict[str, int]
    verified_remaining: dict[str, int]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "ukg.workspace-erasure-receipt.v1",
            "workspace_id": self.workspace_id,
            "erased_at": self.erased_at,
            "deleted": self.deleted,
            "verified_remaining": self.verified_remaining,
            "verified": all(value == 0 for value in self.verified_remaining.values()),
        }


class PrivacyRepository(Protocol):
    async def export_workspace(self, workspace_id: str) -> dict[str, list[dict[str, Any]]]: ...

    async def erase_workspace(self, workspace_id: str) -> WorkspaceErasureReceipt: ...

    async def close(self) -> None: ...


def validate_workspace_id(workspace_id: str) -> str:
    normalized = workspace_id.strip()
    if not normalized:
        raise ValueError("workspace_id_required")
    if len(normalized) > 128:
        raise ValueError("workspace_id_too_long")
    return normalized


def _canonical_bytes(value: Any) -> bytes:
    return orjson.dumps(value, option=orjson.OPT_SORT_KEYS | orjson.OPT_UTC_Z)


def build_workspace_export(
    workspace_id: str,
    records: dict[str, list[dict[str, Any]]],
    *,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    workspace_id = validate_workspace_id(workspace_id)
    unexpected = sorted(set(records) - set(_PRIVACY_TABLES))
    missing = sorted(set(_PRIVACY_TABLES) - set(records))
    if unexpected or missing:
        raise PrivacyVerificationError(
            f"privacy_export_table_contract_mismatch:missing={missing}:unexpected={unexpected}"
        )

    for table, rows in records.items():
        for row in rows:
            scoped_workspace = row.get("workspace_id")
            if scoped_workspace is not None and scoped_workspace != workspace_id:
                raise PrivacyVerificationError(
                    f"privacy_export_cross_workspace_row:{table}:{scoped_workspace}"
                )

    record_hash = sha256(_canonical_bytes(records)).hexdigest()
    effective_generated_at = generated_at or datetime.now(UTC)
    if effective_generated_at.tzinfo is None or effective_generated_at.utcoffset() is None:
        raise ValueError("generated_at_must_be_timezone_aware")

    return {
        "schema": PRIVACY_EXPORT_SCHEMA,
        "workspace_id": workspace_id,
        "generated_at": effective_generated_at,
        "counts": {table: len(records[table]) for table in _PRIVACY_TABLES},
        "records_sha256": record_hash,
        "records": records,
    }


def serialize_workspace_export(export: dict[str, Any]) -> bytes:
    return _canonical_bytes(export) + b"\n"


class PostgresPrivacyRepository:
    """Workspace-scoped DSAR/export and hard-erasure operations for the durable store."""

    def __init__(self, database_url: str | None = None) -> None:
        configured_url = database_url or get_settings().database_url
        self._engine: AsyncEngine = create_async_engine(configured_url, pool_pre_ping=True)

    async def _rows(self, statement: str, workspace_id: str) -> list[dict[str, Any]]:
        async with self._engine.connect() as connection:
            result = await connection.execute(text(statement), {"workspace_id": workspace_id})
            return [dict(row) for row in result.mappings().all()]

    async def export_workspace(self, workspace_id: str) -> dict[str, list[dict[str, Any]]]:
        workspace_id = validate_workspace_id(workspace_id)
        documents = await self._rows(
            """
            select id, workspace_id, source, external_id, title, body, metadata, access,
                   created_at, retention_until, deleted_at, purge_after, deletion_reason
            from documents
            where workspace_id = :workspace_id
            order by id
            """,
            workspace_id,
        )
        chunks = await self._rows(
            """
            select id, document_id, workspace_id, text, ordinal, metadata, access,
                   embedding::text as embedding
            from chunks
            where workspace_id = :workspace_id
            order by id
            """,
            workspace_id,
        )
        entities = await self._rows(
            """
            select id, workspace_id, document_id, name, type, metadata, access
            from entities
            where workspace_id = :workspace_id
            order by id
            """,
            workspace_id,
        )
        relationships = await self._rows(
            """
            select id, workspace_id, document_id, subject, predicate, object,
                   evidence_chunk_id, confidence, metadata, access
            from relationships
            where workspace_id = :workspace_id
            order by id
            """,
            workspace_id,
        )
        return {
            "documents": documents,
            "chunks": chunks,
            "entities": entities,
            "relationships": relationships,
        }

    async def _counts(self, connection: Any, workspace_id: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        for table in _PRIVACY_TABLES:
            result = await connection.execute(
                text(f"select count(*) as count from {table} where workspace_id = :workspace_id"),
                {"workspace_id": workspace_id},
            )
            counts[table] = int(result.scalar_one())
        return counts

    async def erase_workspace(self, workspace_id: str) -> WorkspaceErasureReceipt:
        workspace_id = validate_workspace_id(workspace_id)
        erased_at = datetime.now(UTC)
        async with self._engine.begin() as connection:
            before = await self._counts(connection, workspace_id)

            # Delete child/graph rows explicitly so the receipt reflects every store touched.
            for table in ("relationships", "entities", "chunks", "documents"):
                await connection.execute(
                    text(f"delete from {table} where workspace_id = :workspace_id"),
                    {"workspace_id": workspace_id},
                )

            remaining = await self._counts(connection, workspace_id)
            if any(remaining.values()):
                raise PrivacyVerificationError(
                    f"workspace_erasure_verification_failed:{workspace_id}:{remaining}"
                )

        return WorkspaceErasureReceipt(
            workspace_id=workspace_id,
            erased_at=erased_at,
            deleted=before,
            verified_remaining=remaining,
        )

    async def close(self) -> None:
        await self._engine.dispose()


class WorkspacePrivacyService:
    def __init__(self, repository: PrivacyRepository | None = None) -> None:
        self.repository = repository or PostgresPrivacyRepository()

    async def export(self, workspace_id: str) -> dict[str, Any]:
        normalized = validate_workspace_id(workspace_id)
        records = await self.repository.export_workspace(normalized)
        return build_workspace_export(normalized, records)

    async def erase(self, workspace_id: str) -> WorkspaceErasureReceipt:
        normalized = validate_workspace_id(workspace_id)
        receipt = await self.repository.erase_workspace(normalized)
        if any(receipt.verified_remaining.values()):
            raise PrivacyVerificationError(
                f"workspace_erasure_verification_failed:{normalized}:{receipt.verified_remaining}"
            )
        return receipt

    async def close(self) -> None:
        await self.repository.close()
