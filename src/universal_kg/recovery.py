from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator


class DeletionLedgerEntry(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    workspace_id: str = Field(min_length=1, max_length=128)
    document_id: str = Field(min_length=1, max_length=128)
    source: str = Field(min_length=1, max_length=64)
    external_id: str | None = Field(default=None, max_length=512)
    deleted_at: datetime
    purge_after: datetime
    reason: str = Field(min_length=1, max_length=1000)

    @field_validator("deleted_at", "purge_after")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("deletion ledger timestamps must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_order(self) -> DeletionLedgerEntry:
        if self.purge_after < self.deleted_at:
            raise ValueError("purge_after must not precede deleted_at")
        return self


class DeletionLedgerArtifact(BaseModel):
    schema: Literal["ukg-deletion-ledger-v1"] = "ukg-deletion-ledger-v1"
    workspace_id: str = Field(min_length=1, max_length=128)
    exported_at: datetime
    entries: list[DeletionLedgerEntry]
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("exported_at")
    @classmethod
    def require_export_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("exported_at must include a timezone")
        return value


def _canonical_payload(
    workspace_id: str,
    exported_at: datetime,
    entries: list[DeletionLedgerEntry],
) -> bytes:
    payload = {
        "schema": "ukg-deletion-ledger-v1",
        "workspace_id": workspace_id,
        "exported_at": exported_at.isoformat(),
        "entries": [
            entry.model_dump(mode="json")
            for entry in sorted(entries, key=lambda item: (item.deleted_at, item.event_id))
        ],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def build_deletion_ledger_artifact(
    workspace_id: str,
    exported_at: datetime,
    entries: list[DeletionLedgerEntry],
) -> DeletionLedgerArtifact:
    if exported_at.tzinfo is None or exported_at.utcoffset() is None:
        raise ValueError("exported_at must include a timezone")
    for entry in entries:
        if entry.workspace_id != workspace_id:
            raise ValueError("deletion ledger artifact contains a different workspace")
    ordered = sorted(entries, key=lambda item: (item.deleted_at, item.event_id))
    digest = hashlib.sha256(_canonical_payload(workspace_id, exported_at, ordered)).hexdigest()
    return DeletionLedgerArtifact(
        workspace_id=workspace_id,
        exported_at=exported_at,
        entries=ordered,
        sha256=digest,
    )


def serialize_deletion_ledger_artifact(artifact: DeletionLedgerArtifact) -> str:
    return artifact.model_dump_json(indent=2)


def parse_deletion_ledger_artifact(raw: str) -> DeletionLedgerArtifact:
    artifact = DeletionLedgerArtifact.model_validate_json(raw)
    expected = hashlib.sha256(
        _canonical_payload(artifact.workspace_id, artifact.exported_at, artifact.entries)
    ).hexdigest()
    if not hashlib.compare_digest(expected, artifact.sha256):
        raise ValueError("deletion ledger artifact checksum mismatch")
    return artifact
