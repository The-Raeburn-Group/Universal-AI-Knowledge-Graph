"""Create the persistent knowledge graph and vector store.

Revision ID: 20260905_0001
Revises:
Create Date: 2026-09-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "20260905_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("create extension if not exists vector")

    op.create_table(
        "documents",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("external_id", sa.String(length=512), nullable=True),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_documents_workspace_id", "documents", ["workspace_id"])
    op.create_index(
        "documents_workspace_source_external_idx",
        "documents",
        ["workspace_id", "source", "external_id"],
    )

    op.create_table(
        "chunks",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "document_id",
            sa.Text(),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("embedding", Vector(384), nullable=False),
    )
    op.create_index("ix_chunks_document_id", "chunks", ["document_id"])
    op.create_index("ix_chunks_workspace_id", "chunks", ["workspace_id"])
    op.create_index(
        "chunks_workspace_document_ordinal_idx",
        "chunks",
        ["workspace_id", "document_id", "ordinal"],
    )
    op.create_index(
        "chunks_embedding_hnsw_idx",
        "chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )

    op.create_table(
        "entities",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=512), nullable=False),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.create_index("ix_entities_workspace_id", "entities", ["workspace_id"])
    op.create_index("entities_workspace_name_idx", "entities", ["workspace_id", "name"])

    op.create_table(
        "relationships",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("subject", sa.String(length=512), nullable=False),
        sa.Column("predicate", sa.String(length=256), nullable=False),
        sa.Column("object", sa.String(length=512), nullable=False),
        sa.Column(
            "evidence_chunk_id",
            sa.Text(),
            sa.ForeignKey("chunks.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.CheckConstraint(
            "confidence >= 0.0 and confidence <= 1.0",
            name="relationships_confidence_range",
        ),
    )
    op.create_index("ix_relationships_workspace_id", "relationships", ["workspace_id"])
    op.create_index(
        "relationships_workspace_subject_idx",
        "relationships",
        ["workspace_id", "subject"],
    )
    op.create_index(
        "relationships_workspace_object_idx",
        "relationships",
        ["workspace_id", "object"],
    )


def downgrade() -> None:
    op.drop_index("relationships_workspace_object_idx", table_name="relationships")
    op.drop_index("relationships_workspace_subject_idx", table_name="relationships")
    op.drop_index("ix_relationships_workspace_id", table_name="relationships")
    op.drop_table("relationships")

    op.drop_index("entities_workspace_name_idx", table_name="entities")
    op.drop_index("ix_entities_workspace_id", table_name="entities")
    op.drop_table("entities")

    op.drop_index("chunks_embedding_hnsw_idx", table_name="chunks")
    op.drop_index("chunks_workspace_document_ordinal_idx", table_name="chunks")
    op.drop_index("ix_chunks_workspace_id", table_name="chunks")
    op.drop_index("ix_chunks_document_id", table_name="chunks")
    op.drop_table("chunks")

    op.drop_index("documents_workspace_source_external_idx", table_name="documents")
    op.drop_index("ix_documents_workspace_id", table_name="documents")
    op.drop_table("documents")
