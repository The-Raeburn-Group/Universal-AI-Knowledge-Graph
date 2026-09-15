#!/usr/bin/env python3
"""Create and verify PostgreSQL logical backups for the Knowledge Graph.

The manifest intentionally contains hashes/counts only; it never records database
credentials or customer content.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import psycopg
from psycopg import sql

MANIFEST_SCHEMA = "ukg.postgres.backup-manifest.v1"
TABLE_EXPRESSIONS: dict[str, tuple[str, ...]] = {
    "documents": (
        "id",
        "workspace_id",
        "source",
        "external_id",
        "title",
        "body",
        "metadata",
        "access",
        "created_at",
        "retention_until",
        "deleted_at",
        "purge_after",
        "deletion_reason",
    ),
    "chunks": (
        "id",
        "document_id",
        "workspace_id",
        "text",
        "ordinal",
        "metadata",
        "access",
        "embedding::text AS embedding",
    ),
    "entities": (
        "id",
        "workspace_id",
        "name",
        "type",
        "metadata",
        "access",
        "document_id",
    ),
    "relationships": (
        "id",
        "workspace_id",
        "subject",
        "predicate",
        "object",
        "evidence_chunk_id",
        "confidence",
        "metadata",
        "access",
        "document_id",
    ),
}


def normalise_database_url(database_url: str) -> str:
    """Convert the SQLAlchemy psycopg scheme into libpq's PostgreSQL scheme."""
    if database_url.startswith("postgresql+psycopg://"):
        return "postgresql://" + database_url.removeprefix("postgresql+psycopg://")
    if database_url.startswith("postgresql://"):
        return database_url
    raise ValueError("Only PostgreSQL database URLs are supported for backup/restore.")


def _resolve_database_url(explicit: str | None, env_name: str) -> str:
    database_url = explicit or os.getenv(env_name)
    if not database_url:
        raise RuntimeError(f"Provide --database-url or set {env_name}.")
    return database_url


def _libpq_target(database_url: str) -> tuple[list[str], dict[str, str]]:
    parsed = urlparse(normalise_database_url(database_url))
    database = unquote(parsed.path.lstrip("/"))
    if not database:
        raise ValueError("Database URL must include a database name.")

    args: list[str] = []
    if parsed.hostname:
        args.extend(["--host", parsed.hostname])
    if parsed.port:
        args.extend(["--port", str(parsed.port)])
    if parsed.username:
        args.extend(["--username", unquote(parsed.username)])
    args.extend(["--dbname", database])

    env = os.environ.copy()
    if parsed.password is not None:
        env["PGPASSWORD"] = unquote(parsed.password)

    query = parse_qs(parsed.query)
    libpq_env = {
        "sslmode": "PGSSLMODE",
        "sslrootcert": "PGSSLROOTCERT",
        "sslcert": "PGSSLCERT",
        "sslkey": "PGSSLKEY",
    }
    for key, env_key in libpq_env.items():
        values = query.get(key)
        if values:
            env[env_key] = values[-1]
    return args, env


def _run_pg_tool(tool: str, database_url: str, extra_args: Sequence[str]) -> None:
    if tool not in {"pg_dump", "pg_restore"}:
        raise ValueError(f"Unsupported PostgreSQL tool: {tool}")
    executable = shutil.which(tool)
    if executable is None:
        raise RuntimeError(f"{tool} is required but was not found on PATH.")
    target_args, env = _libpq_target(database_url)
    command = [executable, *target_args, *extra_args]
    subprocess.run(command, check=True, env=env)  # noqa: S603


def _canonical(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _canonical(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    if isinstance(value, bytes):
        return value.hex()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _table_snapshot(connection: psycopg.Connection[Any], table: str) -> dict[str, Any]:
    expressions = TABLE_EXPRESSIONS[table]
    query = sql.SQL("SELECT {} FROM {} ORDER BY id").format(
        sql.SQL(", ").join(sql.SQL(expression) for expression in expressions),
        sql.Identifier(table),
    )
    digest = hashlib.sha256()
    count = 0
    with connection.cursor() as cursor:
        cursor.execute(query)
        for row in cursor:
            payload = json.dumps(
                [_canonical(value) for value in row],
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            digest.update(len(payload).to_bytes(8, "big"))
            digest.update(payload)
            count += 1
    return {"rows": count, "sha256": digest.hexdigest()}


def snapshot_database(database_url: str) -> dict[str, Any]:
    with psycopg.connect(normalise_database_url(database_url)) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT version_num FROM alembic_version")
            revision_row = cursor.fetchone()
            if revision_row is None:
                raise RuntimeError("alembic_version is missing from the database.")
            cursor.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            vector_row = cursor.fetchone()
            if vector_row is None:
                raise RuntimeError("pgvector extension is missing from the database.")
        return {
            "alembic_revision": str(revision_row[0]),
            "vector_extension_version": str(vector_row[0]),
            "tables": {
                table: _table_snapshot(connection, table) for table in TABLE_EXPRESSIONS
            },
        }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def create_backup(database_url: str, dump_path: Path, manifest_path: Path) -> None:
    dump_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    before = snapshot_database(database_url)
    _run_pg_tool(
        "pg_dump",
        database_url,
        [
            "--format=custom",
            "--no-owner",
            "--no-acl",
            "--compress=6",
            f"--file={dump_path}",
        ],
    )
    after = snapshot_database(database_url)
    if before != after:
        dump_path.unlink(missing_ok=True)
        raise RuntimeError(
            "Source database changed during logical backup; retry from a quiesced/read-only window."
        )
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "created_at": datetime.now().astimezone().isoformat(),
        "dump_sha256": sha256_file(dump_path),
        "snapshot": before,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")


def _load_manifest(manifest_path: Path) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("schema") != MANIFEST_SCHEMA:
        raise RuntimeError("Unsupported or invalid backup manifest schema.")
    if not isinstance(manifest.get("dump_sha256"), str) or not isinstance(
        manifest.get("snapshot"), dict
    ):
        raise RuntimeError("Backup manifest is incomplete.")
    return manifest


def restore_backup(
    database_url: str,
    dump_path: Path,
    manifest_path: Path,
    *,
    allow_destructive_restore: bool,
) -> None:
    if not allow_destructive_restore:
        raise RuntimeError("Restore is destructive; pass --allow-destructive-restore explicitly.")
    manifest = _load_manifest(manifest_path)
    if sha256_file(dump_path) != manifest["dump_sha256"]:
        raise RuntimeError("Backup dump hash does not match its manifest; restore aborted.")
    _run_pg_tool(
        "pg_restore",
        database_url,
        [
            "--clean",
            "--if-exists",
            "--exit-on-error",
            "--single-transaction",
            "--no-owner",
            "--no-acl",
            str(dump_path),
        ],
    )


def verify_restore(database_url: str, dump_path: Path, manifest_path: Path) -> None:
    manifest = _load_manifest(manifest_path)
    if sha256_file(dump_path) != manifest["dump_sha256"]:
        raise RuntimeError("Backup dump hash does not match its manifest.")
    restored = snapshot_database(database_url)
    if restored != manifest["snapshot"]:
        raise RuntimeError(
            "Restored database does not match the source snapshot recorded in the backup manifest."
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    for command in ("backup", "verify"):
        child = subparsers.add_parser(command)
        child.add_argument("--database-url")
        child.add_argument("--dump", required=True, type=Path)
        child.add_argument("--manifest", required=True, type=Path)

    restore = subparsers.add_parser("restore")
    restore.add_argument("--database-url")
    restore.add_argument("--dump", required=True, type=Path)
    restore.add_argument("--manifest", required=True, type=Path)
    restore.add_argument("--allow-destructive-restore", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    env_name = "UKG_RESTORE_DATABASE_URL" if args.command == "restore" else "UKG_DATABASE_URL"
    database_url = _resolve_database_url(args.database_url, env_name)
    if args.command == "backup":
        create_backup(database_url, args.dump, args.manifest)
    elif args.command == "restore":
        restore_backup(
            database_url,
            args.dump,
            args.manifest,
            allow_destructive_restore=args.allow_destructive_restore,
        )
    elif args.command == "verify":
        verify_restore(database_url, args.dump, args.manifest)
    else:  # pragma: no cover - argparse enforces the command choices.
        raise RuntimeError(f"Unknown command: {args.command}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
