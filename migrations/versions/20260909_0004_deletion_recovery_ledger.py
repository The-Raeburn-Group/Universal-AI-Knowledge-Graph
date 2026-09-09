"""Add append-only deletion recovery ledger.

Revision ID: 20260909_0004
Revises: 20260909_0003
Create Date: 2026-09-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260909_0004"
down_revision: str | None = "20260909_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "deletion_ledger",
        sa.Column("event_id", sa.Text(), primary_key=True),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("document_id", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("external_id", sa.String(length=512), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("purge_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "document_id",
            name="deletion_ledger_workspace_document_key",
        ),
    )
    op.create_index(
        "deletion_ledger_workspace_deleted_idx",
        "deletion_ledger",
        ["workspace_id", "deleted_at"],
    )

    op.execute(
        """
        INSERT INTO deletion_ledger (
            event_id,
            workspace_id,
            document_id,
            source,
            external_id,
            deleted_at,
            purge_after,
            reason
        )
        SELECT
            gen_random_uuid()::text,
            workspace_id,
            id,
            source,
            external_id,
            deleted_at,
            purge_after,
            COALESCE(deletion_reason, 'legacy_tombstone')
        FROM documents
        WHERE deleted_at IS NOT NULL
          AND purge_after IS NOT NULL
        ON CONFLICT (workspace_id, document_id) DO NOTHING
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION ukg_record_document_deletion()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF OLD.deleted_at IS NULL
               AND NEW.deleted_at IS NOT NULL
               AND NEW.purge_after IS NOT NULL THEN
                INSERT INTO deletion_ledger (
                    event_id,
                    workspace_id,
                    document_id,
                    source,
                    external_id,
                    deleted_at,
                    purge_after,
                    reason
                ) VALUES (
                    gen_random_uuid()::text,
                    NEW.workspace_id,
                    NEW.id,
                    NEW.source,
                    NEW.external_id,
                    NEW.deleted_at,
                    NEW.purge_after,
                    COALESCE(NEW.deletion_reason, 'deleted')
                )
                ON CONFLICT (workspace_id, document_id) DO NOTHING;
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER documents_deletion_ledger_trigger
        AFTER UPDATE OF deleted_at, purge_after ON documents
        FOR EACH ROW
        EXECUTE FUNCTION ukg_record_document_deletion()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS documents_deletion_ledger_trigger ON documents")
    op.execute("DROP FUNCTION IF EXISTS ukg_record_document_deletion()")
    op.drop_index("deletion_ledger_workspace_deleted_idx", table_name="deletion_ledger")
    op.drop_table("deletion_ledger")
