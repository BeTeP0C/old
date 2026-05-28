from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.roadmap_priority import CODE_TITLE_RU
from app.core.roles import EmployeeRole
from app.models.employee import Employee
from app.models.notification import (
    NOTIFICATION_SEVERITY_HIGH,
    NOTIFICATION_SEVERITY_MEDIUM,
    NOTIFICATION_STATUS_DELIVERED,
    NOTIFICATION_TYPE_ROADMAP_REQUEST,
    Notification,
)
from app.models.roadmap_item import (
    ROADMAP_SUBJECT_EMPLOYEE,
    ROADMAP_SUBJECT_TEAM,
    RoadmapItem,
)
from app.repositories.employees import EmployeeRepository
from app.repositories.notifications import NotificationRepository
from app.repositories.team_members import TeamMemberRepository
from app.repositories.work_schedules import WorkScheduleRepository
from app.services.exceptions import InvalidOperationError, NotFoundError
from app.services.notification_ai import NotificationAIGenerator
from app.services.notification_delivery import decide_delivery
from app.services.notification_recipients import RecipientResolver


@dataclass(frozen=True)
class NotificationDraft:
    """Сырой материал, из которого собираются 1..N Notification-записей.

    Используется метрик-триггером: одна метрика порождает один Draft, а сервис
    сам решает кого, когда и в каком виде уведомить.
    """

    type: str
    severity: str
    title: str
    body: str
    subject_type: str  # "employee" | "team"
    subject_id: UUID
    dedup_bucket: str  # обычно ISO-дата, например "2026-05-28"
    payload: dict | None = None
    related_roadmap_item_id: UUID | None = None


def make_dedup_key(draft: NotificationDraft, recipient_id: UUID) -> str:
    return f"{draft.type}:{draft.subject_type}:{draft.subject_id}:{recipient_id}:{draft.dedup_bucket}"


class NotificationService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        ai_generator: NotificationAIGenerator | None = None,
    ) -> None:
        self.session = session
        self.repo = NotificationRepository(session)
        self.employees = EmployeeRepository(session)
        self.team_members = TeamMemberRepository(session)
        self.schedules = WorkScheduleRepository(session)
        self.recipients = RecipientResolver(
            employees=self.employees, team_members=self.team_members
        )
        # ai_generator может быть мок-объектом в тестах или None в окружениях
        # без OPENROUTER_API_KEY — в обоих случаях работаем по rule-based fallback.
        self.ai_generator = ai_generator or NotificationAIGenerator()

    # --- Smart-эмит ----------------------------------------------------------
    async def emit(
        self, draft: NotificationDraft, *, now: datetime | None = None
    ) -> list[Notification]:
        """Создаёт уведомления для рассчитанного списка получателей.

        Применяет: (1) выбор адресатов (RecipientResolver), (2) выбор момента
        (decide_delivery), (3) дедупликацию по dedup_key. Возвращает только те
        уведомления, которые реально были вставлены (без дублей).
        """
        now = now or datetime.now(UTC)

        if draft.subject_type == ROADMAP_SUBJECT_EMPLOYEE:
            recipients = await self.recipients.for_employee_event(
                employee_id=draft.subject_id, severity=draft.severity
            )
        elif draft.subject_type == ROADMAP_SUBJECT_TEAM:
            recipients = await self.recipients.for_team_event(
                team_id=draft.subject_id, severity=draft.severity
            )
        else:
            raise InvalidOperationError(f"unknown subject_type: {draft.subject_type}")

        created: list[Notification] = []
        for recipient in recipients:
            schedule = await self.schedules.get_active_for_employee(recipient.id)
            decision = decide_delivery(
                recipient=recipient,
                schedule=schedule,
                severity=draft.severity,
                now=now,
            )
            # AI выбирает «причину» (title/body) на основе контекста; при
            # любой проблеме (нет ключа, сетевая ошибка, плохой JSON)
            # генератор возвращает None и мы используем rule-based текст.
            generated = await self.ai_generator.generate(draft=draft, recipient=recipient)
            title = generated.title if generated else draft.title
            body = generated.body if generated else draft.body
            notification = await self._insert_with_dedup(
                draft=draft,
                recipient=recipient,
                title=title,
                body=body,
                status=decision.status,
                deferred_until=decision.deferred_until,
            )
            if notification is not None:
                created.append(notification)

        return created

    async def _insert_with_dedup(
        self,
        *,
        draft: NotificationDraft,
        recipient: Employee,
        title: str,
        body: str,
        status: str,
        deferred_until: datetime | None,
    ) -> Notification | None:
        dedup_key = make_dedup_key(draft, recipient.id)
        notification = Notification(
            recipient_id=recipient.id,
            type=draft.type,
            title=title,
            body=body,
            payload=draft.payload,
            related_roadmap_item_id=draft.related_roadmap_item_id,
            dedup_key=dedup_key,
            severity=draft.severity,
            status=status,
            deferred_until=deferred_until,
        )
        # Полагаемся на уникальный индекс uq_notifications_dedup_key: если
        # такая запись за выбранный bucket уже была, INSERT упадёт
        # IntegrityError — ловим и считаем «подавлено политикой дедупликации».
        # Важно: используем SAVEPOINT (begin_nested), чтобы откатить только
        # эту вставку, а не всю транзакцию — иначе вызывающие нас сервисы
        # (например MetricCalculator) теряют свои изменения.
        try:
            async with self.session.begin_nested():
                self.session.add(notification)
        except IntegrityError:
            return None
        return notification

    # --- Legacy / ручные вызовы ----------------------------------------------
    async def create_for_roadmap_request(
        self, item: RoadmapItem, actor: Employee
    ) -> list[Notification]:
        """Старый путь — оставлен, чтобы не ломать roadmap.update_status.

        Использует ту же smart-логику (RecipientResolver + decide_delivery),
        но severity подбирается из item.severity, а dedup-bucket — id итема,
        чтобы повторный «requested» не плодил дубликатов.
        """
        severity = item.severity if item.severity in {
            "low", NOTIFICATION_SEVERITY_MEDIUM, NOTIFICATION_SEVERITY_HIGH, "critical"
        } else NOTIFICATION_SEVERITY_MEDIUM

        draft = NotificationDraft(
            type=NOTIFICATION_TYPE_ROADMAP_REQUEST,
            severity=severity,
            title=self._title_for(item),
            body=item.action,
            subject_type=item.subject_type,
            subject_id=item.employee_id or item.team_id or item.id,
            dedup_bucket=str(item.id),
            payload={
                "roadmap_item_id": str(item.id),
                "recommendation_code": item.recommendation_code,
                "severity": item.severity,
                "requested_by": str(actor.id),
            },
            related_roadmap_item_id=item.id,
        )
        return await self.emit(draft)

    async def list_for_recipient(
        self,
        recipient_id: UUID,
        *,
        unread_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Notification]:
        return await self.repo.list_for_recipient(
            recipient_id, unread_only=unread_only, limit=limit, offset=offset
        )

    async def mark_as_read(
        self, notification_id: UUID, actor: Employee
    ) -> Notification:
        notification = await self.repo.get(notification_id)
        if notification is None:
            raise NotFoundError("notification not found")
        if notification.recipient_id != actor.id and actor.role != EmployeeRole.ADMIN:
            raise InvalidOperationError("cannot read others' notifications")
        notification = await self.repo.mark_as_read(notification)
        # Доставленным считаем уведомление с момента, когда пользователь его увидел.
        if notification.status != NOTIFICATION_STATUS_DELIVERED:
            notification.status = NOTIFICATION_STATUS_DELIVERED
            notification.deferred_until = None
            await self.session.flush()
        await self.session.commit()
        return notification

    @staticmethod
    def _title_for(item: RoadmapItem) -> str:
        label = CODE_TITLE_RU.get(item.recommendation_code, item.recommendation_code)
        return f"Запрос актуализации: {label}"
