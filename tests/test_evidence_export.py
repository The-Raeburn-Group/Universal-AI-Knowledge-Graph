from __future__ import annotations

from datetime import UTC, datetime

from universal_kg.domain import CitationProvenance, SearchHit, SearchResponse
from universal_kg.evidence_export import (
    EVIDENCE_EXPORT_VERSION,
    build_evidence_export,
    verify_evidence_export_bundle,
)


def search_response(
    *,
    source_uri: str | None = "https://example.test/policy",
    source_version: str | None = "v7",
    metadata: dict[str, object] | None = None,
) -> SearchResponse:
    text = "The approved control threshold is 75 percent."
    hit = SearchHit(
        document_id="doc-1",
        chunk_id="chunk-1",
        title="Approved policy",
        text=text,
        score=1.0,
        source="manual",
        metadata=metadata or {"evidence_source_type": "primary"},
        citation=CitationProvenance(
            workspace_id="workspace-a",
            source="manual",
            document_id="doc-1",
            chunk_id="chunk-1",
            source_uri=source_uri,
            source_version=source_version,
            retrieved_at=datetime(2026, 9, 25, 18, 0, tzinfo=UTC),
            content_sha256=__import__("hashlib").sha256(text.encode("utf-8")).hexdigest(),
        ),
    )
    return SearchResponse(query="approved control threshold", hits=[hit])


def test_builds_versioned_integrity_bound_evidence_export() -> None:
    bundle = build_evidence_export(search_response(), workspace_id="workspace-a")

    assert bundle.contract_version == EVIDENCE_EXPORT_VERSION
    assert bundle.workspace_id == "workspace-a"
    assert len(bundle.sources) == 1
    source = bundle.sources[0]
    assert source.id == "chunk-1"
    assert source.uri == "https://example.test/policy"
    assert source.source_type == "primary"
    assert source.document_version == "v7"
    assert source.content_hash == source.content_hash.lower()
    assert len(source.content_hash) == 64
    assert verify_evidence_export_bundle(bundle)


def test_uses_stable_urn_and_document_hash_when_source_metadata_is_unversioned() -> None:
    document_hash = "a" * 64
    bundle = build_evidence_export(
        search_response(
            source_uri=None,
            source_version=None,
            metadata={
                "content_sha256": document_hash,
                "evidence_source_type": "internal",
                "source_acl_ref": "drive:file-1:acl-v3",
            },
        ),
        workspace_id="workspace-a",
    )

    source = bundle.sources[0]
    assert source.uri == "urn:raeburnai:kg:workspace-a:doc-1:chunk-1"
    assert source.document_version == f"sha256:{document_hash}"
    assert source.source_type == "internal"
    assert source.source_acl_ref is None
    assert verify_evidence_export_bundle(bundle)


def test_never_guesses_source_authority_from_connector_name() -> None:
    bundle = build_evidence_export(
        search_response(metadata={"system": "government-registry"}),
        workspace_id="workspace-a",
    )
    assert bundle.sources[0].source_type == "unknown"


def test_bundle_digest_detects_transport_or_excerpt_tampering() -> None:
    bundle = build_evidence_export(search_response(), workspace_id="workspace-a")
    assert verify_evidence_export_bundle(bundle)

    tampered = bundle.model_copy(deep=True)
    tampered.sources[0].excerpt = "The approved control threshold is 10 percent."
    assert not verify_evidence_export_bundle(tampered)

    tampered_query = bundle.model_copy(deep=True)
    tampered_query.query = "different query"
    assert not verify_evidence_export_bundle(tampered_query)


def test_export_uses_chunk_hash_fallback_when_document_version_evidence_is_absent() -> None:
    bundle = build_evidence_export(
        search_response(source_uri=None, source_version=None, metadata={}),
        workspace_id="workspace-a",
    )
    source = bundle.sources[0]
    assert source.document_version == f"chunk-sha256:{source.content_hash}"
    assert verify_evidence_export_bundle(bundle)
