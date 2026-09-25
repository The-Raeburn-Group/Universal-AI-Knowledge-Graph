"""Add indexed PostgreSQL full-text retrieval for chunks.

Revision ID: 20260925_0004
Revises: 20260909_0003
Create Date: 2026-09-25
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260925_0004"
down_revision: str | None = "20260909_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE INDEX chunks_text_fts_gin_idx
        ON chunks
        USING gin (to_tsvector('simple'::regconfig, text))
        """
    )


def downgrade() -> None:
    op.drop_index("chunks_text_fts_gin_idx", table_name="chunks")
