from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.employee import Employee
    from app.models.roadmap_item import RoadmapItem


NOTIFICATION_TYPE_ROADMAP_REQUEST = "roadmap_actualization_request"
NOTIFICATION_TYPE_ROADMAP_STATUS_CHANGED = "roadmap_status_changed"
NOTIFICATION_TYPE_RESCHEDULE_PROPOSAL = "reschedule_proposal"
NOTIFICATION_TYPE_RISK_INCREASED = "risk_level_increased"
NOTIFICATION_TYPE_SCHEDULE_OUTDATED = "schedule_outdated"

NOTIFICATION_SEVERITY_LOW = "low"
NOTIFICATION_SEVERITY_MEDIUM = "medium"
NOTIFICATION_SEVERITY_HIGH = "high"
NOTIFICATION_SEVERITY_CRITICAL = "critical"

NOTIFICATION_STATUS_PENDING = "pending"
NOTIFICATION_STATUS_DELIVERED = "delivered"
NOTIFICATION_STATUS_DEFERRED = "deferred"
NOTIFICATION_STATUS_SUPPRESSED = "suppressed"


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    recipient_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("employees.id", ondelete="CASCADE"),
        nullable=False,
    )
    type: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    related_roadmap_item_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("roadmap_items.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Идемпотентность: уникальный ключ вида "{type}:{recipient}:{subject_id}:{bucket}",
    # где bucket = «сутки» (UTC) для дневной дедупликации. Уникальный индекс
    # ниже не даёт вставить второе уведомление с тем же ключом, поэтому код
    # сервиса может смело попытаться создать — DB-side гарантия.
    dedup_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="medium")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="delivered")
    # Когда уведомление создано вне рабочего окна получателя, мы фиксируем его
    # со статусом "deferred" и временем, до которого его не показываем.
    deferred_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    recipient: Mapped["Employee"] = relationship(
        "Employee",
        back_populates="notifications",
        foreign_keys=[recipient_id],
    )
    related_roadmap_item: Mapped["RoadmapItem | None"] = relationship(
        "RoadmapItem",
        back_populates="notifications",
        foreign_keys=[related_roadmap_item_id],
    )

    __table_args__ = (
        Index("ix_notifications_recipient_id", "recipient_id"),
        Index(
            "uq_notifications_dedup_key",
            "dedup_key",
            unique=True,
            postgresql_where=Column("dedup_key").is_not(None),
        ),
    )
