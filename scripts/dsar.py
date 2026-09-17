from __future__ import annotations

import argparse
import asyncio
from hashlib import sha256
from pathlib import Path

import orjson

from universal_kg.privacy import (
    PostgresPrivacyRepository,
    WorkspacePrivacyService,
    serialize_workspace_export,
    validate_workspace_id,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Workspace-scoped DSAR export and verified erasure utility."
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="Optional SQLAlchemy PostgreSQL URL; defaults to UKG_DATABASE_URL.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    export = subcommands.add_parser("export", help="Export all durable KG data for one workspace.")
    export.add_argument("--workspace", required=True)
    export.add_argument("--output", required=True, type=Path)

    erase = subcommands.add_parser(
        "erase",
        help="Hard-delete one workspace from durable KG tables and verify zero rows remain.",
    )
    erase.add_argument("--workspace", required=True)
    erase.add_argument(
        "--confirm-workspace",
        required=True,
        help="Must exactly match --workspace to authorize destructive erasure.",
    )
    erase.add_argument("--receipt", required=True, type=Path)
    return parser


def _write_private_file(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    path.chmod(0o600)


async def _export(database_url: str | None, workspace: str, output: Path) -> None:
    repository = PostgresPrivacyRepository(database_url)
    service = WorkspacePrivacyService(repository)
    try:
        exported = await service.export(workspace)
        payload = serialize_workspace_export(exported)
        _write_private_file(output, payload)
        digest = sha256(payload).hexdigest()
        checksum_path = output.with_name(f"{output.name}.sha256")
        checksum_line = f"{digest}  {output.name}\n".encode()
        _write_private_file(checksum_path, checksum_line)
        print(
            f"exported workspace={exported['workspace_id']} "
            f"records_sha256={exported['records_sha256']} output={output}"
        )
    finally:
        await service.close()


async def _erase(
    database_url: str | None,
    workspace: str,
    confirm_workspace: str,
    receipt_path: Path,
) -> None:
    normalized = validate_workspace_id(workspace)
    if confirm_workspace != normalized:
        raise SystemExit("--confirm-workspace must exactly match the normalized --workspace value")

    repository = PostgresPrivacyRepository(database_url)
    service = WorkspacePrivacyService(repository)
    try:
        receipt = await service.erase(normalized)
        payload = orjson.dumps(
            receipt.as_dict(),
            option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS | orjson.OPT_UTC_Z,
        ) + b"\n"
        _write_private_file(receipt_path, payload)
        print(
            f"erased workspace={normalized} verified=true "
            f"deleted={receipt.deleted} receipt={receipt_path}"
        )
    finally:
        await service.close()


def main() -> None:
    args = _parser().parse_args()
    if args.command == "export":
        asyncio.run(_export(args.database_url, args.workspace, args.output))
        return
    if args.command == "erase":
        asyncio.run(
            _erase(
                args.database_url,
                args.workspace,
                args.confirm_workspace,
                args.receipt,
            )
        )
        return
    raise SystemExit(f"unsupported command: {args.command}")


if __name__ == "__main__":
    main()
