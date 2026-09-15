#!/usr/bin/env python3
"""Seed a deterministic backup/restore fixture into a migrated Knowledge Graph database."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import psycopg

from postgres_dr import normalise_database_url

WORKSPACE_ID = "dr-workspace"
ACTIVE_DOCUMENT_ID = "dr-active-document"
DELETED_DOCUMENT_ID = "dr-deleted-document"
ACTIVE_CHUNK_ID = "dr-active-chunk"
ACTIVE_ENTITY_ID = "dr-active-entity"
ACTIVE_RELATIONSHIP_ID = "dr-active-relationship"


def seed(database_url: str) -> None:
    access = {
        "visibility": "restricted",
        "principals": ["dr-user"],
        "roles": ["researcher"],
        "groups": ["dr-group"],
        "source_acl_ref": "dr-acl-v1",
    }
    metadata = {"fixture": "backup-restore", "source_uri": "dr://active-document"}
    vector_literal = "[" + ",".join("0" for _ in range(384)) + "]"
    now = datetime.now(UTC)

    with psycopg.connect(normalise_database_url(database_url)) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM documents WHERE id = ANY(%s)",
                ([ACTIVE_DOCUMENT_ID, DELETED_DOCUMENT_ID],),
            )
            cursor.execute(
                """
                INSERT INTO documents (
                    id, workspace_id, source, external_id, title, body, metadata, access,
                    retention_until
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s)
                """,
                (
                    ACTIVE_DOCUMENT_ID,
                    WORKSPACE_ID,
                    "dr-fixture",
                    "active-1",
                    "DR active document",
                    "Backup and restore must preserve this restricted document.",
                    json.dumps(metadata),
                    json.dumps(access),
                    now + timedelta(days=30),
                ),
            )
            cursor.execute(
                """
                INSERT INTO chunks (
                    id, document_id, workspace_id, text, ordinal, metadata, access, embedding
                )
                VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::vector)
                """,
                (
                    ACTIVE_CHUNK_ID,
                    ACTIVE_DOCUMENT_ID,
                    WORKSPACE_ID,
                    "Restricted backup restore evidence.",
                    0,
                    json.dumps(metadata),
                    json.dumps(access),
                    vector_literal,
                ),
            )
            cursor.execute(
                """
                INSERT INTO entities (
                    id, workspace_id, name, type, metadata, access, document_id
                )
                VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s)
                """,
                (
                    ACTIVE_ENTITY_ID,
                    WORKSPACE_ID,
                    "Raeburn DR Fixture",
                    "test",
                    json.dumps(metadata),
                    json.dumps(access),
                    ACTIVE_DOCUMENT_ID,
                ),
            )
            cursor.execute(
                """
                INSERT INTO relationships (
                    id, workspace_id, subject, predicate, object, evidence_chunk_id,
                    confidence, metadata, access, document_id
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s)
                """,
                (
                    ACTIVE_RELATIONSHIP_ID,
                    WORKSPACE_ID,
                    "Raeburn DR Fixture",
                    "verifies",
                    "backup restore integrity",
                    ACTIVE_CHUNK_ID,
                    1.0,
                    json.dumps(metadata),
                    json.dumps(access),
                    ACTIVE_DOCUMENT_ID,
                ),
            )
            cursor.execute(
                """
                INSERT INTO documents (
                    id, workspace_id, source, external_id, title, body, metadata, access,
                    deleted_at, purge_after, deletion_reason
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s)
                """,
                (
                    DELETED_DOCUMENT_ID,
                    WORKSPACE_ID,
                    "dr-fixture",
                    "deleted-1",
                    "DR tombstoned document",
                    "Tombstone state must survive backup and restore.",
                    json.dumps({"fixture": "backup-restore", "state": "tombstoned"}),
                    json.dumps(access),
                    now - timedelta(minutes=1),
                    now + timedelta(days=7),
                    "dr_restore_fixture",
                ),
            )
        connection.commit()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    seed(args.database_url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
