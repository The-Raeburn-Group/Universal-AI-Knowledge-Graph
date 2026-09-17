# DSAR export and workspace erasure

This document describes the Knowledge Graph portion of RaeburnAI privacy operations. It is an engineering control, not a statement that a request has been completed across every RaeburnAI service or subprocessor.

## Scope

The durable Knowledge Graph stores workspace-scoped data in four PostgreSQL tables:

- `documents` — source content, metadata, access policy and lifecycle fields;
- `chunks` — derived text chunks, metadata, access policy and embedding vectors;
- `entities` — extracted graph entities and metadata;
- `relationships` — extracted graph relationships, evidence references and metadata.

`scripts/dsar.py` operates only on those four stores and always requires a single explicit workspace ID.

## Data export

Use an export before erasure when the privacy request, legal basis or support procedure requires a copy of the data:

```bash
python scripts/dsar.py export \
  --workspace tenant-123 \
  --output build/privacy/tenant-123-export.json
```

The export contains:

- a versioned schema identifier;
- the exact workspace ID;
- generation timestamp;
- per-table record counts;
- all workspace-scoped rows from the four durable stores;
- embeddings represented as text so the derived vector data is included;
- a deterministic SHA-256 digest over the `records` object.

A second `<output>.sha256` file covers the complete serialized export. Both files are created with owner-only permissions (`0600`).

The service also verifies that no returned row carrying a `workspace_id` belongs to a different workspace. A cross-workspace row aborts the export rather than being silently included.

## Hard erasure

Hard erasure is deliberately separate from normal document tombstoning/retention. It is intended for an approved workspace-level privacy deletion operation.

```bash
python scripts/dsar.py erase \
  --workspace tenant-123 \
  --confirm-workspace tenant-123 \
  --receipt build/privacy/tenant-123-erasure-receipt.json
```

Safety properties:

1. `--confirm-workspace` must exactly match the normalized workspace ID.
2. All SQL uses bound workspace parameters; the workspace ID is never interpolated into a query.
3. Relationships, entities, chunks and documents are deleted inside one database transaction.
4. Before commit, the operation recounts every workspace-scoped table.
5. If any row remains, the operation raises `PrivacyVerificationError` and the transaction rolls back.
6. A receipt is emitted only after all four post-delete counts are zero.

The receipt records the number of rows that existed before deletion and the verified post-delete counts. It does not contain the deleted content.

## Backups, logs and upstream systems

A successful Knowledge Graph erasure does **not** prove deletion from:

- PostgreSQL backups or disaster-recovery snapshots;
- application/security logs outside these tables;
- source systems from which a connector originally retrieved the data;
- Chain, AgentOS, model-provider, MCP or other RaeburnAI stores;
- third-party subprocessors.

Those systems require their own retention/deletion workflow. Backup expiry and restore procedures must also prevent deliberately erased workspace data from being silently reintroduced without a subsequent deletion replay.

## Operational evidence

For an end-to-end privacy request, retain:

- request/case identifier in the approved privacy-management system;
- identity/authority verification outside this utility;
- scope decision and lawful-basis notes;
- Knowledge Graph export checksum when an export was produced;
- verified erasure receipt when deletion was performed;
- evidence from every other in-scope store/subprocessor;
- exceptions or legal holds and their approval/expiry.

Do not place unnecessary personal data in tickets, commit messages, logs or erasure receipts.

## Remaining platform work

This repository slice provides a durable Knowledge Graph export/erasure primitive. Platform completion still requires orchestration across Chain, AgentOS, memory, audit/log stores, connected tools, model/evaluation datasets and subprocessors; backup-expiry/deletion replay; request-status tracking; legal-hold handling; and live PostgreSQL integration evidence.
