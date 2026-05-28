from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, Text, TypeDecorator, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

AI_EMBEDDING_DIMENSION = 1536

try:
    from pgvector.sqlalchemy import Vector as _PgVectorType  # type: ignore[import-untyped]
except ImportError:
    _PgVectorType = None


class _FallbackVectorType(TypeDecorator[list[float] | None]):
    impl = Text
    cache_ok = True

    def __init__(self, dim: int | None = None, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.dim = dim


def _embedding_column_type() -> Any:
    if _PgVectorType is not None:
        return _PgVectorType(AI_EMBEDDING_DIMENSION)
    return _FallbackVectorType(AI_EMBEDDING_DIMENSION)


if TYPE_CHECKING:
    from app.models.ai_document import AiDocument


class AiChunk(Base):
    __tablename__ = "ai_chunks"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    document_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("ai_documents.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(
        _embedding_column_type(),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    document: Mapped[AiDocument] = relationship("AiDocument", back_populates="chunks")
