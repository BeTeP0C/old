from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import (
    NOTIFICATION_SEVERITY_MEDIUM,
    NOTIFICATION_TYPE_SCHEDULE_CONFIRMED,
    NOTIFICATION_TYPE_SCHEDULE_DECLINED,
)
from app.models.roadmap_item import ROADMAP_SUBJECT_EMPLOYEE
from app.models.schedule_confirmation_request import (
    CONFIRMATION_STATUS_DECLINED,
    CONFIRMATION_STATUS_PENDING,
    ScheduleConfirmationRequest,
)
from app.repositories.employees import EmployeeRepository
from app.repositories.schedule_confirmation_requests import (
    ScheduleConfirmationRequestRepository,
)
from app.repositories.work_schedules import WorkScheduleRepository
from app.schemas.schedule_confirmation import (
    ScheduleConfirmResponse,
    ScheduleConfirmationRequestResponse,
)
from app.services.exceptions import InvalidOperationError, NotFoundError
from app.services.metrics_recalc import recalc_actuality
from app.services.notification_hub import hub
from app.services.notifications import NotificationService


class ScheduleConfirmationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.employees = EmployeeRepository(session)
        self.schedules = WorkScheduleRepository(session)
        self.requests = ScheduleConfirmationRequestRepository(session)
        self.notifications = NotificationService(session)

    async def confirm(self, employee_id: UUID) -> ScheduleConfirmResponse:
        employee = await self.employees.get(employee_id)
        if employee is None:
            raise NotFoundError("employee not found")
        schedule = await self.schedules.get_active_for_employee(employee_id)
        if schedule is None:
            raise NotFoundError("active schedule not found")

        now = datetime.now(timezone.utc)
        # Кто просил подтвердить — нужно зафиксировать ДО закрытия запросов,
        # чтобы уведомить инициаторов («админу не приходило подтверждение»).
        pending = await self.requests.list_by_employee(employee_id, CONFIRMATION_STATUS_PENDING)
        requester_ids = {
            request.requested_by_id
            for request in pending
            if request.requested_by_id is not None and request.requested_by_id != employee_id
        }
        employee_name = employee.full_name

        schedule.confirmed_at = now
        closed_ids = await self.requests.mark_all_pending_as_confirmed(employee_id, now=now)
        await recalc_actuality(self.session, employee_id)

        notified: list[UUID] = []
        for requester_id in requester_ids:
            created = await self.notifications.notify_recipient(
                recipient_id=requester_id,
                type=NOTIFICATION_TYPE_SCHEDULE_CONFIRMED,
                severity=NOTIFICATION_SEVERITY_MEDIUM,
                title="График подтверждён",
                body=f"{employee_name} подтвердил(а) свой рабочий график.",
                subject_type=ROADMAP_SUBJECT_EMPLOYEE,
                subject_id=employee_id,
                dedup_bucket=f"confirm:{now.isoformat()}",
                payload={"employee_id": str(employee_id), "confirmed_at": now.isoformat()},
                now=now,
            )
            if created is not None:
                notified.append(requester_id)

        await self.session.commit()
        await _signal_recipients(notified)
        return ScheduleConfirmResponse(confirmed_at=now, closed_request_ids=closed_ids)

    async def create_bulk(
        self,
        employee_ids: list[UUID],
        requested_by_id: UUID | None,
        reason: str | None,
    ) -> tuple[list[ScheduleConfirmationRequest], list[UUID]]:
        """Создаёт pending-запросы пачкой. Пропускает employee_id, у которых уже есть pending.

        Returns (created, skipped_ids) — created содержит только реально созданные запросы.
        """
        created: list[ScheduleConfirmationRequest] = []
        skipped: list[UUID] = []
        for employee_id in employee_ids:
            if await self.employees.get(employee_id) is None:
                skipped.append(employee_id)
                continue
            existing = await self.requests.get_pending_for_employee(employee_id)
            if existing is not None:
                skipped.append(employee_id)
                continue
            request = ScheduleConfirmationRequest(
                employee_id=employee_id,
                requested_by_id=requested_by_id,
                reason=reason,
                status=CONFIRMATION_STATUS_PENDING,
            )
            request = await self.requests.create(request)
            created.append(request)
        if created:
            await self.session.commit()
            # Перечитываем созданные запросы со всеми relations для корректной сериализации.
            loaded: list[ScheduleConfirmationRequest] = []
            for req in created:
                fresh = await self.requests.get_by_id(req.id)
                loaded.append(fresh or req)
            created = loaded
        return created, skipped



    async def create_request(
        self,
        employee_id: UUID,
        requested_by_id: UUID | None,
        reason: str | None,
    ) -> tuple[ScheduleConfirmationRequest, bool]:
        """Создаёт запрос или возвращает существующий pending.

        Returns (request, created). created=False, если уже был pending — caller
        должен вернуть 409 Conflict.
        """
        if await self.employees.get(employee_id) is None:
            raise NotFoundError("employee not found")

        existing = await self.requests.get_pending_for_employee(employee_id)
        if existing is not None:
            return existing, False

        request = ScheduleConfirmationRequest(
            employee_id=employee_id,
            requested_by_id=requested_by_id,
            reason=reason,
            status=CONFIRMATION_STATUS_PENDING,
        )
        request = await self.requests.create(request)
        await self.session.commit()
        # Re-fetch with relations for response serialization.
        loaded = await self.requests.get_by_id(request.id)
        return (loaded or request), True

    async def list_requests(
        self,
        employee_id: UUID,
        status_filter: str | None = None,
    ) -> list[ScheduleConfirmationRequest]:
        if await self.employees.get(employee_id) is None:
            raise NotFoundError("employee not found")
        return await self.requests.list_by_employee(employee_id, status_filter)

    async def decline(
        self,
        employee_id: UUID,
        request_id: UUID,
        note: str | None,
    ) -> ScheduleConfirmationRequest:
        request = await self.requests.get_by_id(request_id)
        if request is None or request.employee_id != employee_id:
            raise NotFoundError("confirmation request not found")
        if request.status != CONFIRMATION_STATUS_PENDING:
            raise InvalidOperationError("request is not pending")
        now = datetime.now(timezone.utc)
        request.status = CONFIRMATION_STATUS_DECLINED
        request.responded_at = now
        request.response_note = note

        # Уведомляем инициатора запроса об отклонении (если это не он сам).
        requester_id = request.requested_by_id
        employee = await self.employees.get(employee_id)
        employee_name = employee.full_name if employee is not None else "Сотрудник"
        notified: list[UUID] = []
        if requester_id is not None and requester_id != employee_id:
            body = f"{employee_name} отклонил(а) запрос на подтверждение графика."
            if note:
                body += f" Причина: {note}"
            created = await self.notifications.notify_recipient(
                recipient_id=requester_id,
                type=NOTIFICATION_TYPE_SCHEDULE_DECLINED,
                severity=NOTIFICATION_SEVERITY_MEDIUM,
                title="Запрос на подтверждение отклонён",
                body=body,
                subject_type=ROADMAP_SUBJECT_EMPLOYEE,
                subject_id=employee_id,
                dedup_bucket=f"decline:{request_id}",
                payload={"employee_id": str(employee_id), "request_id": str(request_id)},
                now=now,
            )
            if created is not None:
                notified.append(requester_id)

        await self.session.commit()
        await _signal_recipients(notified)
        return request


async def _signal_recipients(recipient_ids: list[UUID]) -> None:
    """Шлём realtime-сигнал каждому адресату ПОСЛЕ коммита (источник истины — БД)."""
    for recipient_id in recipient_ids:
        await hub.signal(recipient_id)


def to_response(
    request: ScheduleConfirmationRequest,
) -> ScheduleConfirmationRequestResponse:
    return ScheduleConfirmationRequestResponse(
        id=request.id,
        employee_id=request.employee_id,
        requested_by_id=request.requested_by_id,
        requested_by_name=(
            request.requested_by.full_name if request.requested_by is not None else None
        ),
        employee_name=(request.employee.full_name if request.employee is not None else None),
        reason=request.reason,
        status=request.status,
        created_at=request.created_at,
        responded_at=request.responded_at,
        response_note=request.response_note,
    )
