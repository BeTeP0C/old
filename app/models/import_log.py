from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ImportLog(Base):
    """Журнал загрузок данных для страницы /upload («Последние загрузки»).

    Сами события лежат в activity_events; здесь — аудит попыток импорта
    (источник, файл, статус, счётчики), чтобы история переживала перезагрузку
    страницы и была видна со всех устройств.
    """

    __tablename__ = "import_logs"
    __table_args__ = (Index("ix_import_logs_created_at", "created_at"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    # Код источника: calendar | hr | tracker | timesheet | unknown.
    source: Mapped[str] = mapped_column(String(30), nullable=False)
    file_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # ok | partial | error.
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    imported_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped_duplicate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # created_by — мягкая ссылка на employees.id без FK: журнал должен жить,
    # даже если сотрудник-актор позже удалён (как changed_by в change_history).
    created_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    # has_file — есть ли скачиваемый исходник (дешёвый флаг для списка).
    has_file: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # content — исходный текст файла. deferred: не тянем мегабайты в list-запросах,
    # грузим только при скачивании конкретной строки.
    content: Mapped[str | None] = mapped_column(Text, nullable=True, deferred=True)
