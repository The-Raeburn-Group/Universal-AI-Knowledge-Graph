# Knowledge data lifecycle

Universal AI Knowledge Graph treats deletion as a two-stage lifecycle so data stops influencing AI retrieval immediately while physical purge can remain operationally controlled.

## Retention at ingestion

`POST /v1/ingest` accepts an optional `retention_days` value between 1 and 3650. When supplied, the service records an absolute `retention_until` timestamp from the ingestion time. When omitted, no automatic retention deadline is created by this service.

Retention is workspace-scoped. A retention run never operates across workspaces.

## Tombstone semantics

An authorised lifecycle administrator can call:

`POST /v1/documents/{document_id}/tombstone`

with the delegated workspace/actor headers and a body containing `workspace_id` and `reason`.

The service records:

- `deleted_at`
- `purge_after`
- `deletion_reason`

The configured purge grace is `UKG_RETENTION_PURGE_GRACE_DAYS` and defaults to 30 days.

Tombstoning is idempotent. Repeating the operation does not extend the purge date or overwrite the original deletion reason.

## Immediate retrieval exclusion

A tombstoned document is excluded immediately from semantic/vector retrieval. Entities and relationships are now bound to their source document and graph retrieval joins through an active, non-tombstoned document, so tombstoned source material cannot continue to appear through graph context.

Migration `20260909_0003` backfills relationship document provenance from evidence chunks and backfills entity provenance only when exactly one source document can be proven. Legacy graph records that cannot be safely attributed are not returned by the new graph retrieval path; they should be reingested or explicitly remapped rather than guessed.

## Retention worker contract

An authorised lifecycle administrator can call:

`POST /v1/retention/run`

for one workspace. An optional timezone-aware `as_of` timestamp can be supplied for deterministic operations/testing; otherwise current UTC time is used.

A run performs two actions transactionally within the store:

1. active documents with `retention_until <= as_of` are tombstoned with reason `retention_expired`;
2. tombstoned documents with `purge_after <= as_of` are physically deleted.

PostgreSQL foreign keys cascade physical deletion to chunks, embeddings, source-bound entities and relationships.

## Authorization

Lifecycle endpoints require all normal internal API/delegated identity checks plus one of these normalized roles:

- `admin`
- `kg.admin`
- `data.admin`

A service credential alone is not sufficient to delete knowledge.

## Audit behavior

The API emits audit events for explicit tombstones and retention runs, including workspace, actor, document/reason or run counts. This repository's current audit sink remains the platform's existing audit-event mechanism; durable centralized audit retention is tracked separately.

## Backups and recovery

This lifecycle does not claim immediate erasure from historical backups. Production backup retention, restore rehearsal, purge propagation into backup policy, legal holds and documented RPO/RTO remain separate production-readiness work. A restore process must not silently re-expose records whose tombstone/purge state is newer than the restored snapshot.
