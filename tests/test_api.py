from __future__ import annotations

from fastapi.testclient import TestClient

from universal_kg.api.main import app


def delegated_headers(
    workspace_id: str,
    actor_id: str = "tester@example.com",
    *,
    roles: str | None = None,
    groups: str | None = None,
) -> dict[str, str]:
    headers = {
        "X-Workspace-ID": workspace_id,
        "X-Actor-ID": actor_id,
    }
    if roles:
        headers["X-Actor-Roles"] = roles
    if groups:
        headers["X-Actor-Groups"] = groups
    return headers


def test_health_and_ready() -> None:
    client = TestClient(app)
    assert client.get("/health").status_code == 200
    assert client.get("/ready").json() == {"status": "ready"}


def test_end_to_end_ingest_then_search() -> None:
    client = TestClient(app)
    headers = delegated_headers("e2e")
    ingest_response = client.post(
        "/v1/ingest",
        headers=headers,
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
        headers=headers,
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


def test_restricted_document_is_filtered_by_delegated_identity() -> None:
    client = TestClient(app)
    workspace = "e2e-restricted"
    owner_headers = delegated_headers(workspace, "alice@example.com")
    ingest_response = client.post(
        "/v1/ingest",
        headers=owner_headers,
        json={
            "workspace_id": workspace,
            "source": "crm",
            "title": "Aurora confidential",
            "body": "Project Aurora Confidential is only for Alice.",
            "metadata": {"system": "crm"},
            "access": {
                "visibility": "restricted",
                "principals": ["alice@example.com"],
                "roles": [],
                "groups": [],
                "source_acl_ref": "crm-acl:aurora:v2",
            },
        },
    )
    assert ingest_response.status_code == 200
    assert "access" not in ingest_response.json()

    outsider = client.post(
        "/v1/search",
        headers=delegated_headers(workspace, "bob@example.com"),
        json={"workspace_id": workspace, "query": "Aurora Confidential", "limit": 10},
    )
    assert outsider.status_code == 200
    assert outsider.json()["hits"] == []
    assert outsider.json()["related_entities"] == []

    owner = client.post(
        "/v1/search",
        headers=owner_headers,
        json={"workspace_id": workspace, "query": "Aurora Confidential", "limit": 10},
    )
    assert owner.status_code == 200
    assert owner.json()["hits"]
    assert all("access" not in entity for entity in owner.json()["related_entities"])


def test_requires_delegated_identity_and_rejects_workspace_spoofing() -> None:
    client = TestClient(app)
    missing = client.post(
        "/v1/search",
        json={"workspace_id": "e2e-auth", "query": "test", "limit": 5},
    )
    assert missing.status_code == 401

    mismatch = client.post(
        "/v1/search",
        headers=delegated_headers("workspace-b"),
        json={"workspace_id": "workspace-a", "query": "test", "limit": 5},
    )
    assert mismatch.status_code == 403


def test_rejects_invalid_workspace() -> None:
    client = TestClient(app)
    response = client.post(
        "/v1/search",
        headers=delegated_headers("bad-workspace"),
        json={"workspace_id": "bad workspace", "query": "test", "limit": 5},
    )
    assert response.status_code == 422
