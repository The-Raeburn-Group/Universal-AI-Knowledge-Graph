"""Persist source ACLs for permission-aware retrieval.

Revision ID: 20260908_0002
Revises: 20260905_0001
Create Date: 2026-09-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260908_0002"
down_revision: str | None = "20260905_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFAULT_ACCESS = (
    "jsonb_build_object("
    "'visibility', 'workspace', "
    "'principals', '[]'::jsonb, "
    "'roles', '[]'::jsonb, "
    "'groups', '[]'::jsonb, "
    "'source_acl_ref', NULL"
    ")"
)


def upgrade() -> None:
    for table in ("documents", "chunks", "entities", "relationships"):
        op.add_column(
            table,
            sa.Column(
                "access",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text(_DEFAULT_ACCESS),
            ),
        )
        op.create_index(
            f"{table}_access_gin_idx",
            table,
            ["access"],
            postgresql_using="gin",
        )


def downgrade() -> None:
    for table in ("relationships", "entities", "chunks", "documents"):
        op.drop_index(f"{table}_access_gin_idx", table_name=table)
        op.drop_column(table, "access")
