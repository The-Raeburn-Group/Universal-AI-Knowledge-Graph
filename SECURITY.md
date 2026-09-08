# Security Policy

## Supported versions

The `main` branch is the actively supported development line.

## Reporting a vulnerability

Please do not open a public issue for security vulnerabilities.

Email the maintainers with:

- A clear description of the issue
- Reproduction steps
- Impact assessment
- Affected version or commit
- Suggested fix if available

## Security design principles

- Do not commit secrets.
- Use environment variables or a managed secret store.
- Keep connectors least-privilege.
- Log operational metadata, not raw sensitive content.
- Preserve source provenance for auditability.
- Encrypt data at rest in production infrastructure.
- Use TLS for all deployed API traffic.
- Production requires persistent PostgreSQL and a strong internal service API key.
- Delegated workspace/actor/role/group headers must only be originated by an authenticated trusted gateway.

## Permission-aware retrieval

Knowledge is not considered workspace-readable merely because it was successfully ingested.
Every document carries an explicit access policy which is inherited by its chunks, extracted
entities and relationships. The current policy supports:

- workspace-visible records;
- explicit principal IDs;
- role membership;
- group membership; and
- an opaque `source_acl_ref` for tracing the source-system permission/version used at ingestion.

The API requires `X-Workspace-ID` and `X-Actor-ID` for ingest/search calls and optionally accepts
`X-Actor-Roles` and `X-Actor-Groups` from the trusted service boundary. The requested workspace
must exactly match the delegated workspace. These headers are authorization context, not
self-asserted browser/client claims; deployments must prevent direct untrusted callers from
supplying them without the authenticated gateway/service credential.

PostgreSQL retrieval applies workspace and ACL predicates before vector ranking/limit selection.
Graph entity/relationship retrieval applies the same policy before context is assembled. This is
intentional: fetching prohibited rows and filtering them only after ranking could leak existence,
scores or graph structure and could suppress accessible evidence from the top-k result set.

Existing rows upgraded from the original schema are explicitly backfilled as workspace-visible,
which preserves historical behavior. Real source connectors must map source permissions to the
`access` object during ingestion before their permission-inheritance work can be considered
production-ready.

## Connector safety

Connectors may process sensitive enterprise data. New connectors must document:

- Required scopes and permissions
- Data read and write behaviour
- Rate limits
- Retention implications
- How credentials are stored and rotated
- How source ACLs are resolved, versioned and propagated to the knowledge `access` policy
