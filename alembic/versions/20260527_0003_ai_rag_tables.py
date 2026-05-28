"""add ai rag tables

Revision ID: 20260527_0003
Revises: 20260524_0002
Create Date: 2026-05-27
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260527_0003"
down_revision: str | None = "20260524_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


class Vector(sa.UserDefinedType[Any]):
    cache_ok = True

    def __init__(self, dimension: int) -> None:
        self.dimension = dimension

    def get_col_spec(self, **_kw: object) -> str:
        return f"vector({self.dimension})"


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "ai_documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("source_type", sa.String(length=50), nullable=False),
        sa.Column("source_name", sa.String(length=255), nullable=True),
        sa.Column("content_hash", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("content_hash"),
    )
    op.create_index("ix_ai_documents_source_type", "ai_documents", ["source_type"], unique=False)
    op.create_table(
        "ai_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(1536), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["document_id"], ["ai_documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_chunks_document_id", "ai_chunks", ["document_id"], unique=False)
    op.create_index("ix_ai_chunks_chunk_index", "ai_chunks", ["chunk_index"], unique=False)
    # TODO: add an ivfflat/hnsw vector index after choosing production embedding model.


def downgrade() -> None:
    op.drop_index("ix_ai_chunks_chunk_index", table_name="ai_chunks")
    op.drop_index("ix_ai_chunks_document_id", table_name="ai_chunks")
    op.drop_table("ai_chunks")
    op.drop_index("ix_ai_documents_source_type", table_name="ai_documents")
    op.drop_table("ai_documents")
