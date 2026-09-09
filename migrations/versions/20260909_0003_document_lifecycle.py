"""Add document tombstone and retention lifecycle fields.

Revision ID: 20260909_0003
Revises: 20260908_0002
Create Date: 2026-09-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260909_0003"
down_revision: str | None = "20260908_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("retention_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("purge_after", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("deletion_reason", sa.Text(), nullable=True),
    )
    op.create_index(
        "documents_workspace_retention_idx",
        "documents",
        ["workspace_id", "retention_until"],
    )
    op.create_index("ix_documents_deleted_at", "documents", ["deleted_at"])
    op.create_index("ix_documents_purge_after", "documents", ["purge_after"])

    op.add_column("entities", sa.Column("document_id", sa.Text(), nullable=True))
    op.add_column("relationships", sa.Column("document_id", sa.Text(), nullable=True))

    op.execute(
        """
        UPDATE relationships AS r
        SET document_id = c.document_id
        FROM chunks AS c
        WHERE r.evidence_chunk_id = c.id
          AND r.document_id IS NULL
        """
    )
    op.execute(
        """
        WITH entity_documents AS (
            SELECT
                e.id AS entity_id,
                min(r.document_id) AS document_id
            FROM entities AS e
            JOIN relationships AS r
              ON r.workspace_id = e.workspace_id
             AND (r.subject = e.name OR r.object = e.name)
            WHERE r.document_id IS NOT NULL
            GROUP BY e.id
            HAVING count(DISTINCT r.document_id) = 1
        )
        UPDATE entities AS e
        SET document_id = entity_documents.document_id
        FROM entity_documents
        WHERE e.id = entity_documents.entity_id
          AND e.document_id IS NULL
        """
    )

    op.create_foreign_key(
        "entities_document_id_fkey",
        "entities",
        "documents",
        ["document_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "relationships_document_id_fkey",
        "relationships",
        "documents",
        ["document_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_entities_document_id", "entities", ["document_id"])
    op.create_index("ix_relationships_document_id", "relationships", ["document_id"])


def downgrade() -> None:
    op.drop_index("ix_relationships_document_id", table_name="relationships")
    op.drop_index("ix_entities_document_id", table_name="entities")
    op.drop_constraint(
        "relationships_document_id_fkey",
        "relationships",
        type_="foreignkey",
    )
    op.drop_constraint("entities_document_id_fkey", "entities", type_="foreignkey")
    op.drop_column("relationships", "document_id")
    op.drop_column("entities", "document_id")

    op.drop_index("ix_documents_purge_after", table_name="documents")
    op.drop_index("ix_documents_deleted_at", table_name="documents")
    op.drop_index("documents_workspace_retention_idx", table_name="documents")
    op.drop_column("documents", "deletion_reason")
    op.drop_column("documents", "purge_after")
    op.drop_column("documents", "deleted_at")
    op.drop_column("documents", "retention_until")
