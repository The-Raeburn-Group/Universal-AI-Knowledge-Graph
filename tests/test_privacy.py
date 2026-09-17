from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from universal_kg.privacy import (
    PrivacyVerificationError,
    WorkspaceErasureReceipt,
    WorkspacePrivacyService,
    build_workspace_export,
    serialize_workspace_export,
    validate_workspace_id,
)


class FakePrivacyRepository:
    def __init__(
        self,
        records: dict[str, list[dict[str, Any]]],
        receipt: WorkspaceErasureReceipt | None = None,
    ) -> None:
        self.records = records
        self.receipt = receipt
        self.exported_workspace: str | None = None
        self.erased_workspace: str | None = None
        self.closed = False

    async def export_workspace(self, workspace_id: str) -> dict[str, list[dict[str, Any]]]:
        self.exported_workspace = workspace_id
        return self.records

    async def erase_workspace(self, workspace_id: str) -> WorkspaceErasureReceipt:
        self.erased_workspace = workspace_id
        if self.receipt is None:
            raise AssertionError("test receipt required")
        return self.receipt

    async def close(self) -> None:
        self.closed = True


def _records(workspace_id: str = "workspace-a") -> dict[str, list[dict[str, Any]]]:
    return {
        "documents": [
            {
                "id": "doc-1",
                "workspace_id": workspace_id,
                "title": "Example",
                "created_at": datetime(2026, 9, 17, 12, 0, tzinfo=UTC),
            }
        ],
        "chunks": [
            {
                "id": "chunk-1",
                "document_id": "doc-1",
                "workspace_id": workspace_id,
                "text": "personal data",
                "embedding": "[0.1,0.2]",
            }
        ],
        "entities": [{"id": "entity-1", "workspace_id": workspace_id, "name": "Person"}],
        "relationships": [
            {"id": "rel-1", "workspace_id": workspace_id, "subject": "Person"}
        ],
    }


def test_workspace_export_is_complete_and_checksum_is_deterministic() -> None:
    generated_at = datetime(2026, 9, 17, 13, 0, tzinfo=UTC)
    first = build_workspace_export("workspace-a", _records(), generated_at=generated_at)
    second = build_workspace_export("workspace-a", _records(), generated_at=generated_at)

    assert first["schema"] == "ukg.workspace-export.v1"
    assert first["counts"] == {
        "documents": 1,
        "chunks": 1,
        "entities": 1,
        "relationships": 1,
    }
    assert first["records_sha256"] == second["records_sha256"]
    assert serialize_workspace_export(first).endswith(b"\n")


def test_workspace_export_rejects_cross_workspace_rows() -> None:
    records = _records()
    records["chunks"][0]["workspace_id"] = "workspace-b"

    with pytest.raises(PrivacyVerificationError, match="privacy_export_cross_workspace_row"):
        build_workspace_export("workspace-a", records)


def test_workspace_export_requires_all_expected_stores() -> None:
    records = _records()
    records.pop("relationships")

    with pytest.raises(PrivacyVerificationError, match="privacy_export_table_contract_mismatch"):
        build_workspace_export("workspace-a", records)


@pytest.mark.asyncio
async def test_service_normalizes_workspace_and_delegates_export() -> None:
    repository = FakePrivacyRepository(_records())
    service = WorkspacePrivacyService(repository)

    exported = await service.export(" workspace-a ")

    assert repository.exported_workspace == "workspace-a"
    assert exported["workspace_id"] == "workspace-a"


@pytest.mark.asyncio
async def test_service_accepts_zero_verified_erasure_receipt() -> None:
    receipt = WorkspaceErasureReceipt(
        workspace_id="workspace-a",
        erased_at=datetime(2026, 9, 17, 13, 0, tzinfo=UTC),
        deleted={"documents": 1, "chunks": 2, "entities": 3, "relationships": 4},
        verified_remaining={"documents": 0, "chunks": 0, "entities": 0, "relationships": 0},
    )
    repository = FakePrivacyRepository(_records(), receipt)
    service = WorkspacePrivacyService(repository)

    result = await service.erase("workspace-a")

    assert repository.erased_workspace == "workspace-a"
    assert result.as_dict()["verified"] is True
    assert result.deleted["relationships"] == 4


@pytest.mark.asyncio
async def test_service_rejects_unverified_erasure_receipt() -> None:
    receipt = WorkspaceErasureReceipt(
        workspace_id="workspace-a",
        erased_at=datetime(2026, 9, 17, 13, 0, tzinfo=UTC),
        deleted={"documents": 1, "chunks": 1, "entities": 0, "relationships": 0},
        verified_remaining={"documents": 0, "chunks": 1, "entities": 0, "relationships": 0},
    )
    service = WorkspacePrivacyService(FakePrivacyRepository(_records(), receipt))

    with pytest.raises(PrivacyVerificationError, match="workspace_erasure_verification_failed"):
        await service.erase("workspace-a")


def test_workspace_id_validation_rejects_blank_and_oversized_values() -> None:
    with pytest.raises(ValueError, match="workspace_id_required"):
        validate_workspace_id("   ")
    with pytest.raises(ValueError, match="workspace_id_too_long"):
        validate_workspace_id("x" * 129)
