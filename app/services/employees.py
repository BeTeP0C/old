from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.employee import Employee
from app.models.team_member import TeamMember
from app.models.work_schedule import WorkSchedule
from app.repositories.employees import EmployeeRepository
from app.repositories.teams import TeamRepository
from app.schemas.employee import EmployeeCreate, EmployeeFullCreate, EmployeeUpdate
from app.services.audit import (
    ACTION_UPDATE,
    ENTITY_EMPLOYEE,
    employee_to_dict,
    record_change,
)
from app.services.exceptions import InvalidOperationError, NotFoundError


class EmployeeService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.employees = EmployeeRepository(session)

    async def create(self, payload: EmployeeCreate) -> Employee:
        employee = Employee(**payload.model_dump())
        try:
            employee = await self.employees.create(employee)
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise InvalidOperationError(
                "employee with this email or VK user id already exists"
            ) from exc
        # перечитываем, чтобы подтянуть relations (metrics, team_members)
        return await self.get(employee.id)

    async def create_full(self, payload: EmployeeFullCreate) -> Employee:
        """Атомарное создание сотрудника + первичного графика + (опц.) членства в команде.

        Используется wizard'ом «Добавить сотрудника». Всё либо записывается одной
        транзакцией, либо откатывается — никаких «полусозданных» сущностей.
        """
        schedule = payload.schedule
        if schedule.start_time >= schedule.end_time:
            raise InvalidOperationError("start_time must be before end_time")
        if not schedule.work_days:
            raise InvalidOperationError("work_days must not be empty")
        if any(day < 0 or day > 6 for day in schedule.work_days):
            raise InvalidOperationError("work_days values must be in range 0..6")

        if payload.team is not None:
            team = await TeamRepository(self.session).get(payload.team.team_id)
            if team is None:
                raise NotFoundError("team not found")

        employee = Employee(
            vk_user_id=payload.vk_user_id,
            role=payload.role,
            full_name=payload.full_name,
            email=payload.email,
            position=payload.position,
            hire_date=payload.hire_date,
            timezone=payload.timezone,
            work_format=payload.work_format,
            employment_type=payload.employment_type,
        )
        self.session.add(employee)
        try:
            await self.session.flush()

            self.session.add(
                WorkSchedule(
                    employee_id=employee.id,
                    work_days=list(schedule.work_days),
                    start_time=schedule.start_time,
                    end_time=schedule.end_time,
                    timezone=schedule.timezone,
                    work_format=payload.work_format,
                    last_updated_at=datetime.now(tz=UTC),
                    is_active=True,
                )
            )

            if payload.team is not None:
                self.session.add(
                    TeamMember(
                        team_id=payload.team.team_id,
                        employee_id=employee.id,
                        role_in_team=payload.team.role_in_team,
                    )
                )

            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise InvalidOperationError(
                "employee with this email or VK user id already exists"
            ) from exc

        # перечитываем, чтобы подтянуть relations (metrics, team_members, confirmation_requests)
        return await self.get(employee.id)


    async def list(
        self,
        *,
        team_id: UUID | None = None,
        risk_level: str | None = None,
        work_format: str | None = None,
        search: str | None = None,
        category: str | None = None,
        skip: int = 0,
        limit: int | None = None,
    ) -> list[Employee]:
        return await self.employees.list(
            team_id=team_id,
            risk_level=risk_level,
            work_format=work_format,
            search=search,
            category=category,
            skip=skip,
            limit=limit,
        )

    async def count(
        self,
        *,
        team_id: UUID | None = None,
        risk_level: str | None = None,
        work_format: str | None = None,
        search: str | None = None,
        category: str | None = None,
    ) -> int:
        return await self.employees.count(
            team_id=team_id,
            risk_level=risk_level,
            work_format=work_format,
            search=search,
            category=category,
        )

    async def get(self, employee_id: UUID) -> Employee:
        employee = await self.employees.get(employee_id)
        if employee is None:
            raise NotFoundError("employee not found")
        return employee

    async def update(
        self,
        employee_id: UUID,
        payload: EmployeeUpdate,
        *,
        changed_by: UUID,
    ) -> Employee:
        employee = await self.get(employee_id)
        before_snapshot = employee_to_dict(employee)
        values = payload.model_dump(exclude_unset=True)
        try:
            employee = await self.employees.update(employee, values)
            await record_change(
                self.session,
                entity_type=ENTITY_EMPLOYEE,
                entity_id=employee.id,
                employee_id=employee.id,
                action=ACTION_UPDATE,
                changed_by=changed_by,
                before=before_snapshot,
                after=employee_to_dict(employee),
            )
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise InvalidOperationError(
                "employee with this email or VK user id already exists"
            ) from exc
        return await self.get(employee.id)
