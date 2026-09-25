from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256
from typing import Literal, cast

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
        return cast(EvidenceSourceType, value)
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


def _canonical_utc(value: datetime) -> str:
    normalized = value.astimezone(UTC).isoformat()
    return normalized[:-6] + "Z" if normalized.endswith("+00:00") else normalized


def _canonical_bundle_payload(
    *,
    workspace_id: str,
    query: str,
    retrieved_at: datetime,
    sources: list[EvidenceExportSource],
) -> dict[str, object]:
    return {
        "contract_version": EVIDENCE_EXPORT_VERSION,
        "workspace_id": workspace_id,
        "query": query,
        "retrieved_at": _canonical_utc(retrieved_at),
        "sources": [
            {
                "id": source.id,
                "uri": source.uri,
                "title": source.title,
                "source_type": source.source_type,
                "retrieved_at": _canonical_utc(source.retrieved_at),
                "workspace_id": source.workspace_id,
                "document_id": source.document_id,
                "document_version": source.document_version,
                "chunk_id": source.chunk_id,
                "excerpt": source.excerpt,
                "content_hash": source.content_hash,
                "source_acl_ref": source.source_acl_ref,
            }
            for source in sources
        ],
    }


def _bundle_digest_payload(
    *,
    workspace_id: str,
    query: str,
    retrieved_at: datetime,
    sources: list[EvidenceExportSource],
) -> str:
    canonical = json.dumps(
        _canonical_bundle_payload(
            workspace_id=workspace_id,
            query=query,
            retrieved_at=retrieved_at,
            sources=sources,
        ),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def verify_evidence_export_bundle(bundle: EvidenceExportBundle) -> bool:
    expected = _bundle_digest_payload(
        workspace_id=bundle.workspace_id,
        query=bundle.query,
        retrieved_at=bundle.retrieved_at,
        sources=bundle.sources,
    )
    return expected == bundle.bundle_sha256


def build_evidence_export(
    response: SearchResponse,
    *,
    workspace_id: str,
) -> EvidenceExportBundle:
    exported: list[EvidenceExportSource] = []
    for hit in response.hits:
        citation = hit.citation
        if citation is None:
            continue
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
                # ACL identity is intentionally not inferred from caller-controlled
                # metadata. A future trusted store-derived binding can populate this.
                source_acl_ref=None,
            )
        )

    retrieved_at = (
        exported[0].retrieved_at if exported else datetime.now(UTC)
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
