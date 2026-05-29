from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity_event import ActivityEvent
from app.models.notification import (
    NOTIFICATION_SEVERITY_MEDIUM,
    NOTIFICATION_TYPE_EVENT_ASSIGNED,
)
from app.models.roadmap_item import ROADMAP_SUBJECT_EMPLOYEE
from app.repositories.activity_events import ActivityEventRepository
from app.repositories.employees import EmployeeRepository
from app.schemas.activity_event import ActivityEventCreate, ActivityEventImportResult
from app.services.exceptions import InvalidOperationError, NotFoundError
from app.services.notification_hub import hub
from app.services.notifications import NotificationService


class ActivityEventService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.employees = EmployeeRepository(session)
        self.events = ActivityEventRepository(session)
        self.notifications = NotificationService(session)

    async def create_manual(
        self,
        payload: ActivityEventCreate,
        actor_id: UUID | None = None,
    ) -> ActivityEvent:
        event = await self._create_one(payload)
        if event is None:
            raise InvalidOperationError("duplicate activity event external id")

        # Если событие сотруднику завёл КТО-ТО ДРУГОЙ (менеджер/PM/HR) — уведомляем
        # сотрудника, иначе он «не в курсе» о появившемся в календаре событии.
        notify_employee = actor_id is not None and actor_id != payload.employee_id
        if notify_employee:
            actor = await self.employees.get(actor_id)
            actor_name = actor.full_name if actor is not None else "Менеджер"
            await self.notifications.notify_recipient(
                recipient_id=payload.employee_id,
                type=NOTIFICATION_TYPE_EVENT_ASSIGNED,
                severity=NOTIFICATION_SEVERITY_MEDIUM,
                title="Новое событие в календаре",
                body=f"{actor_name} добавил(а) вам событие «{payload.title}».",
                subject_type=ROADMAP_SUBJECT_EMPLOYEE,
                subject_id=payload.employee_id,
                dedup_bucket=f"event:{event.id}",
                payload={
                    "event_id": str(event.id),
                    "title": payload.title,
                    "start_dt": payload.start_dt.isoformat(),
                },
            )

        await self.session.commit()
        if notify_employee:
            await hub.signal(payload.employee_id)
        return event

    async def import_events(self, payloads: list[ActivityEventCreate]) -> ActivityEventImportResult:
        errors: list[str] = []
        imported_count = 0
        skipped_duplicate_count = 0
        employee_ids = list({payload.employee_id for payload in payloads})
        valid_employee_ids = {
            employee.id for employee in await self.employees.list_by_ids(employee_ids)
        }
        external_keys = {
            (payload.source, payload.external_id)
            for payload in payloads
            if payload.external_id is not None
        }
        existing_external_keys = await self.events.list_existing_external_keys(external_keys)
        seen_external_keys: set[tuple[str, str]] = set()

        for index, payload in enumerate(payloads):
            try:
                self._validate_import_payload(payload, valid_employee_ids)
                external_key = (
                    (payload.source, payload.external_id)
                    if payload.external_id is not None
                    else None
                )
                if external_key is not None and (
                    external_key in existing_external_keys or external_key in seen_external_keys
                ):
                    skipped_duplicate_count += 1
                    continue
                if external_key is not None:
                    seen_external_keys.add(external_key)
                await self.events.create(ActivityEvent(**payload.model_dump()))
                imported_count += 1
            except (InvalidOperationError, NotFoundError) as exc:
                errors.append(f"item {index}: {exc}")

        if errors:
            await self.session.rollback()
            raise InvalidOperationError("; ".join(errors))

        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise InvalidOperationError("duplicate activity event external id") from exc

        return ActivityEventImportResult(
            imported_count=imported_count,
            skipped_duplicate_count=skipped_duplicate_count,
            errors=[],
        )

    async def list_for_employee(self, employee_id: UUID) -> list[ActivityEvent]:
        if await self.employees.get(employee_id) is None:
            raise NotFoundError("employee not found")
        return await self.events.list_for_employee(employee_id)

    async def _create_one(self, payload: ActivityEventCreate) -> ActivityEvent | None:
        if payload.start_dt >= payload.end_dt:
            raise InvalidOperationError("start_dt must be earlier than end_dt")
        if await self.employees.get(payload.employee_id) is None:
            raise NotFoundError("employee not found")
        if payload.external_id is not None:
            existing = await self.events.find_by_external_id(payload.source, payload.external_id)
            if existing is not None:
                return None
        return await self.events.create(ActivityEvent(**payload.model_dump()))

    def _validate_import_payload(
        self,
        payload: ActivityEventCreate,
        valid_employee_ids: set[UUID],
    ) -> None:
        if payload.start_dt >= payload.end_dt:
            raise InvalidOperationError("start_dt must be earlier than end_dt")
        if payload.employee_id not in valid_employee_ids:
            raise NotFoundError("employee not found")
