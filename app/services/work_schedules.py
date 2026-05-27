from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.work_schedule import WorkSchedule
from app.repositories.employees import EmployeeRepository
from app.repositories.work_schedules import WorkScheduleRepository
from app.schemas.work_schedule import WorkScheduleCreate
from app.services.audit import (
    ACTION_CREATE,
    ACTION_DEACTIVATE,
    ENTITY_WORK_SCHEDULE,
    record_change,
    schedule_to_dict,
)
from app.services.exceptions import InvalidOperationError, NotFoundError


class WorkScheduleService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.employees = EmployeeRepository(session)
        self.schedules = WorkScheduleRepository(session)

    async def create(
        self,
        employee_id: UUID,
        payload: WorkScheduleCreate,
        *,
        changed_by: UUID,
    ) -> WorkSchedule:
        if payload.employee_id != employee_id:
            raise InvalidOperationError("employee_id in path and body must match")
        if await self.employees.get(employee_id) is None:
            raise NotFoundError("employee not found")

        if payload.is_active:
            current_active = await self.schedules.get_active_for_employee(employee_id)
            if current_active is not None:
                before_snapshot = schedule_to_dict(current_active)
                current_active.is_active = False
                await self.session.flush()
                await record_change(
                    self.session,
                    entity_type=ENTITY_WORK_SCHEDULE,
                    entity_id=current_active.id,
                    employee_id=employee_id,
                    action=ACTION_DEACTIVATE,
                    changed_by=changed_by,
                    before=before_snapshot,
                    after=schedule_to_dict(current_active),
                )

        schedule = await self.schedules.create(WorkSchedule(**payload.model_dump()))
        await record_change(
            self.session,
            entity_type=ENTITY_WORK_SCHEDULE,
            entity_id=schedule.id,
            employee_id=employee_id,
            action=ACTION_CREATE,
            changed_by=changed_by,
            after=schedule_to_dict(schedule),
        )

        # Keep the canonical work_format on Employee in sync with the latest
        # active schedule so list/filter queries on Employee stay correct.
        if payload.is_active:
            employee = await self.employees.get(employee_id)
            if employee is not None and employee.work_format != payload.work_format:
                employee.work_format = payload.work_format
                await self.session.flush()


        await self.session.commit()
        return schedule

    async def get_active(self, employee_id: UUID) -> WorkSchedule:
        if await self.employees.get(employee_id) is None:
            raise NotFoundError("employee not found")
        schedule = await self.schedules.get_active_for_employee(employee_id)
        if schedule is None:
            raise NotFoundError("active schedule not found")
        return schedule
