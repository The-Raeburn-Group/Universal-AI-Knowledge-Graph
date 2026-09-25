from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, Field

from universal_kg.domain import SearchResponse

EVIDENCE_EXPORT_VERSION = "raeburnai.kg-evidence-export.v1"

EvidenceSourceType = Literal["primary", "secondary", "internal", "unknown"]


class EvidenceExportSource(BaseModel):
    id: str = Field(min_length=1, max_length=256)
    uri: str = Field(min_length=1, max_length=2048)
    title: str = Field(min_length=1, max_length=512)
    source_type: EvidenceSourceType
    retrieved_at: datetime
    workspace_id: str = Field(min_length=1, max_length=128)
    document_id: str = Field(min_length=1, max_length=256)
    document_version: str = Field(min_length=1, max_length=512)
    chunk_id: str = Field(min_length=1, max_length=256)
    excerpt: str = Field(min_length=1, max_length=20_000)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_acl_ref: str | None = Field(default=None, max_length=512)


class EvidenceExportBundle(BaseModel):
    contract_version: Literal["raeburnai.kg-evidence-export.v1"] = EVIDENCE_EXPORT_VERSION
    workspace_id: str
    query: str
    retrieved_at: datetime
    sources: list[EvidenceExportSource]
    bundle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def _canonical_source_type(metadata: dict[str, object]) -> EvidenceSourceType:
    value = metadata.get("evidence_source_type")
    if isinstance(value, str) and value in {"primary", "secondary", "internal", "unknown"}:
        return value  # type: ignore[return-value]
    return "unknown"


def _stable_uri(
    *,
    workspace_id: str,
    document_id: str,
    chunk_id: str,
    source_uri: str | None,
) -> str:
    if source_uri:
        return source_uri
    return f"urn:raeburnai:kg:{workspace_id}:{document_id}:{chunk_id}"


def _document_version(
    metadata: dict[str, object],
    source_version: str | None,
    chunk_hash: str,
) -> str:
    if source_version:
        return source_version
    document_hash = metadata.get("content_sha256")
    if (
        isinstance(document_hash, str)
        and len(document_hash) == 64
        and all(character in "0123456789abcdef" for character in document_hash)
    ):
        return f"sha256:{document_hash}"
    return f"chunk-sha256:{chunk_hash}"


def _bundle_digest_payload(
    *,
    workspace_id: str,
    query: str,
    retrieved_at: datetime,
    sources: list[EvidenceExportSource],
) -> str:
    source_payload = [
        "|".join(
            [
                source.id,
                source.uri,
                source.title,
                source.source_type,
                source.retrieved_at.isoformat(),
                source.workspace_id,
                source.document_id,
                source.document_version,
                source.chunk_id,
                source.excerpt,
                source.content_hash,
                source.source_acl_ref or "",
            ]
        )
        for source in sources
    ]
    material = "\n".join(
        [
            EVIDENCE_EXPORT_VERSION,
            workspace_id,
            query,
            retrieved_at.isoformat(),
            *source_payload,
        ]
    )
    return sha256(material.encode("utf-8")).hexdigest()


def build_evidence_export(
    response: SearchResponse,
    *,
    workspace_id: str,
) -> EvidenceExportBundle:
    retrieved_at = datetime.now(UTC)
    exported: list[EvidenceExportSource] = []
    for hit in response.hits:
        citation = hit.citation
        if citation is None:
            continue
        source_acl_ref = hit.metadata.get("source_acl_ref")
        exported.append(
            EvidenceExportSource(
                id=hit.chunk_id,
                uri=_stable_uri(
                    workspace_id=workspace_id,
                    document_id=hit.document_id,
                    chunk_id=hit.chunk_id,
                    source_uri=citation.source_uri,
                ),
                title=hit.title,
                source_type=_canonical_source_type(hit.metadata),
                retrieved_at=citation.retrieved_at,
                workspace_id=workspace_id,
                document_id=hit.document_id,
                document_version=_document_version(
                    hit.metadata,
                    citation.source_version,
                    citation.content_sha256,
                ),
                chunk_id=hit.chunk_id,
                excerpt=hit.text,
                content_hash=citation.content_sha256,
                source_acl_ref=(
                    source_acl_ref if isinstance(source_acl_ref, str) and source_acl_ref else None
                ),
            )
        )

    return EvidenceExportBundle(
        workspace_id=workspace_id,
        query=response.query,
        retrieved_at=retrieved_at,
        sources=exported,
        bundle_sha256=_bundle_digest_payload(
            workspace_id=workspace_id,
            query=response.query,
            retrieved_at=retrieved_at,
            sources=exported,
        ),
    )
