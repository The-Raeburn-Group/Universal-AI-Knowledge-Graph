# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Added

- Durable PostgreSQL + pgvector persistence with versioned Alembic migrations.
- Permissions-aware retrieval with durable source ACLs and delegated workspace/actor enforcement.
- Retrieval provenance, citation identity and untrusted-content security metadata.
- Document retention deadlines, immediate tombstone exclusion and delayed physical purge lifecycle.
- Source-document provenance for graph entities/relationships so deletion removes both vector and graph retrieval paths.
- Admin-only tombstone and workspace retention-run APIs with audit events.

### Security

- Production fails closed without PostgreSQL and a strong internal API key.
- Tombstoned documents are excluded before vector ranking and graph context assembly.
- Legacy graph records without safely attributable source-document provenance are excluded rather than guessed.

### Remaining production work

- Map and refresh real connector ACLs and source deletions.
- Rehearse backup/restore, tombstone replay and purge propagation into backup retention policy.
- Replace static service authentication with the final integrated platform identity boundary where applicable.

## [0.1.0] - 2026-07-02

### Added

- Production FastAPI scaffold.
- Semantic ingestion and search services.
- Deterministic local embedding provider and optional OpenAI provider.
- Connector SDK with JSON and PDF connectors.
- Health, readiness and metrics endpoints.
- Structured JSON logging.
- Basic rate limiting and audit-event logging.
- Dockerfile and Docker Compose stack.
- CI pipeline with linting, type checking, tests, package build and Docker build.
- CodeQL and dependency review configuration.
- Demo data and deployment documentation.

### Known limitations at initial release

- Durable Postgres/pgvector persistence was a production TODO.
- Enterprise SSO/OIDC and RBAC were production TODOs.
- Live Slack, Gmail, CRM, GitHub and SQL connectors were planned roadmap items.
