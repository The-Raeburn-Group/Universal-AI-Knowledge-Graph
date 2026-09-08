from __future__ import annotations

from fastapi.testclient import TestClient

from universal_kg.api.main import app


def test_health_and_ready() -> None:
    client = TestClient(app)
    assert client.get("/health").status_code == 200
    assert client.get("/ready").json() == {"status": "ready"}


def test_end_to_end_ingest_then_search() -> None:
    client = TestClient(app)
    ingest_response = client.post(
        "/v1/ingest",
        json={
            "workspace_id": "e2e",
            "source": "manual",
            "external_id": "slack-export-2026-09-08",
            "title": "Slack export note",
            "body": "The platform team decided that GitHub pull requests need security review.",
            "metadata": {
                "system": "slack",
                "source_uri": "https://slack.example.test/archives/C123/p456",
                "source_version": "export-2026-09-08",
            },
        },
    )
    assert ingest_response.status_code == 200

    search_response = client.post(
        "/v1/search",
        json={"workspace_id": "e2e", "query": "GitHub security review", "limit": 5},
    )
    assert search_response.status_code == 200
    payload = search_response.json()
    assert payload["hits"]
    hit = payload["hits"][0]
    assert hit["provenance"]["origin"] == "knowledge-retrieval"
    assert hit["provenance"]["workspace_id"] == "e2e"
    assert hit["citation"]["workspace_id"] == "e2e"
    assert hit["citation"]["document_id"] == hit["document_id"]
    assert hit["citation"]["chunk_id"] == hit["chunk_id"]
    assert hit["citation"]["source_uri"] == "https://slack.example.test/archives/C123/p456"
    assert hit["citation"]["source_version"] == "export-2026-09-08"
    assert len(hit["citation"]["content_sha256"]) == 64
    assert hit["citation"]["retrieved_at"]
    assert hit["security"]["trust"] == "untrusted"
    assert hit["security"]["instructionAuthority"] == "none"
    assert hit["security"]["handling"] == "data-only"
    assert payload["security"]["trust"] == "untrusted"
    assert payload["security"]["instructionAuthority"] == "none"


def test_rejects_invalid_workspace() -> None:
    client = TestClient(app)
    response = client.post(
        "/v1/search",
        json={"workspace_id": "bad workspace", "query": "test", "limit": 5},
    )
    assert response.status_code == 422
