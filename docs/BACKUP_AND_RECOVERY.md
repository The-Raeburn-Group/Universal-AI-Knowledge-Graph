# PostgreSQL backup and recovery

Universal AI Knowledge Graph uses versioned Alembic migrations and PostgreSQL/pgvector for durable knowledge state. This document defines the repository-level logical backup and restore contract and the evidence required before production recovery can be claimed.

## What the repository now verifies

`make db-backup` creates:

- a PostgreSQL custom-format logical dump;
- a JSON manifest containing the Alembic revision, pgvector extension version, row counts and SHA-256 fingerprints for documents, chunks/vectors, entities and relationships;
- a SHA-256 digest of the dump itself.

The manifest contains no database credentials or document contents.

A backup is rejected when the database changes between the pre-dump and post-dump snapshots. Operational backups therefore need either a quiesced/read-only application window or a provider-managed snapshot/PITR mechanism that supplies an equivalent consistent point in time.

`make db-restore` is deliberately destructive and only targets `UKG_RESTORE_DATABASE_URL`. The underlying restore command requires an explicit destructive-restore flag, verifies the dump hash before changing the target database, and restores in one transaction with owner/ACL portability flags.

`make db-verify-restore` recomputes the restored database snapshot and requires it to match the backup manifest exactly, including ACL JSON, lifecycle/tombstone state, graph provenance and vector text representation.

## Automated restore rehearsal

The PostgreSQL integration job now performs this drill on every change:

1. migrate a fresh PostgreSQL + pgvector database to Alembic head;
2. exercise the existing API/persistent-store tests;
3. seed a restricted document, vector chunk, source-bound entity/relationship and a tombstoned document;
4. create a manifest-verified logical backup;
5. create a separate restore database;
6. restore the dump into that isolated database;
7. prove the restored Alembic revision is current and that running `alembic upgrade head` is safe;
8. compare the restored tables and lifecycle/ACL state to the source manifest.

This is repository-level recovery evidence. It is not a substitute for a production provider's PITR, cross-region backup, encryption, retention or availability controls.

## Operator commands

The source database is taken from `UKG_DATABASE_URL`. The restore target is intentionally separate and is taken from `UKG_RESTORE_DATABASE_URL`, so database passwords do not need to be passed as process arguments.

```sh
export UKG_DATABASE_URL='postgresql+psycopg://.../ukg'
make db-backup

export UKG_RESTORE_DATABASE_URL='postgresql+psycopg://.../ukg_restore'
make db-restore
UKG_DATABASE_URL="$UKG_RESTORE_DATABASE_URL" make db-current
UKG_DATABASE_URL="$UKG_RESTORE_DATABASE_URL" make db-verify-restore
```

Override the default artifact paths when required:

```sh
make db-backup \
  BACKUP_FILE=/secure/path/ukg-2026-09-15.dump \
  BACKUP_MANIFEST=/secure/path/ukg-2026-09-15.manifest.json
```

Backup files can contain customer data and must be stored in an encrypted, access-controlled backup location. They must never be committed to Git.

## RPO/RTO and provider policy

This repository does **not** declare a production RPO or RTO yet. Those targets must be measured against the selected managed PostgreSQL topology and approved commercially before they become service commitments.

Before production launch, the deployment owner must document and test:

- managed PostgreSQL point-in-time recovery and backup frequency;
- encryption at rest and in transit for backups;
- backup location/region and data-residency constraints;
- retention and legal-hold rules;
- credential separation between backup and application roles;
- scheduled restore drills with measured recovery duration;
- alerting for failed/stale backups;
- a recovery runbook covering application freeze, restore, migration, validation and traffic re-enable.

## Tombstones, purges and old backups

A historical backup can predate a later tombstone or physical purge. Restoring such a backup must not silently make deleted data retrievable again.

The current repository drill proves that tombstone state present **at backup time** survives restore. Production recovery still requires a post-restore tombstone/deletion replay source (or an equivalent provider/control-plane mechanism) for deletions that happened after the chosen recovery point. Legal holds must also be evaluated before replaying a purge.

That backup-tombstone replay mechanism remains an explicit production-readiness gate rather than being implied by this logical restore test.
