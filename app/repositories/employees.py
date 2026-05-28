from __future__ import annotations

import builtins
from datetime import datetime
from uuid import UUID

from sqlalchemy import exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.employee import Employee
from app.models.employee_metric import EmployeeMetric
from app.models.schedule_exception import ScheduleException
from app.models.team_member import TeamMember


class EmployeeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, employee: Employee) -> Employee:
        self.session.add(employee)
        await self.session.flush()
        await self.session.refresh(employee)
        return employee

    async def list(
        self,
        *,
        team_id: UUID | None = None,
        risk_level: str | None = None,
        work_format: str | None = None,
        search: str | None = None,
        category: str | None = None,
        now: datetime | None = None,
        skip: int = 0,
        limit: int | None = None,
    ) -> list[Employee]:
        stmt = select(Employee).options(
            selectinload(Employee.metrics),
            selectinload(Employee.team_members),
            selectinload(Employee.confirmation_requests),
        )
        stmt = _apply_filters(
            stmt,
            team_id=team_id,
            risk_level=risk_level,
            work_format=work_format,
            search=search,
            category=category,
            now=now,
        )
        stmt = stmt.order_by(Employee.full_name.asc())
        if skip:
            stmt = stmt.offset(skip)
        if limit is not None:
            stmt = stmt.limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().unique().all())

    async def count(
        self,
        *,
        team_id: UUID | None = None,
        risk_level: str | None = None,
        work_format: str | None = None,
        search: str | None = None,
        category: str | None = None,
        now: datetime | None = None,
    ) -> int:
        stmt = _apply_filters(
            select(Employee.id),
            team_id=team_id,
            risk_level=risk_level,
            work_format=work_format,
            search=search,
            category=category,
            now=now,
        )
        # distinct, чтобы join'ы не раздували счётчик
        count_stmt = select(func.count()).select_from(stmt.distinct().subquery())
        result = await self.session.execute(count_stmt)
        return int(result.scalar_one())

    async def get(self, employee_id: UUID) -> Employee | None:
        result = await self.session.execute(
            select(Employee)
            .options(
                selectinload(Employee.metrics),
                selectinload(Employee.team_members),
                selectinload(Employee.confirmation_requests),
            )
            .where(Employee.id == employee_id)
        )
        return result.scalar_one_or_none()

    async def get_by_vk_user_id(self, vk_user_id: str) -> Employee | None:
        result = await self.session.execute(
            select(Employee).where(Employee.vk_user_id == vk_user_id)
        )
        return result.scalar_one_or_none()

    async def get_by_email(self, email: str) -> Employee | None:
        result = await self.session.execute(select(Employee).where(Employee.email == email))
        return result.scalar_one_or_none()

    async def list_by_ids(self, employee_ids: builtins.list[UUID]) -> builtins.list[Employee]:
        if not employee_ids:
            return []
        result = await self.session.execute(
            select(Employee).where(Employee.id.in_(employee_ids)).order_by(Employee.full_name)
        )
        return list(result.scalars().all())

    async def update(self, employee: Employee, values: dict[str, object]) -> Employee:
        for field, value in values.items():
            setattr(employee, field, value)
        await self.session.flush()
        await self.session.refresh(employee)
        return employee


def _apply_filters(
    stmt,
    *,
    team_id: UUID | None,
    risk_level: str | None,
    work_format: str | None,
    search: str | None,
    category: str | None,
    now: datetime | None,
):
    if team_id is not None:
        stmt = stmt.join(TeamMember, TeamMember.employee_id == Employee.id).where(
            TeamMember.team_id == team_id
        )
    if risk_level is not None:
        stmt = stmt.join(EmployeeMetric, EmployeeMetric.employee_id == Employee.id).where(
            EmployeeMetric.risk_level == risk_level
        )
    if work_format is not None:
        stmt = stmt.where(Employee.work_format == work_format)
    if search:
        pattern = f"%{search.strip()}%"
        stmt = stmt.where(
            or_(
                Employee.full_name.ilike(pattern),
                Employee.email.ilike(pattern),
                Employee.position.ilike(pattern),
            )
        )
    if category is not None:
        stmt = _apply_category_filter(stmt, category, now=now)
    return stmt




def _apply_category_filter(stmt, category: str, *, now: datetime | None):
    """Категории сотрудников из ТЗ §4.

    Все категории требуют join к EmployeeMetric, кроме `in_absence` (EXISTS).
    """
    if category == "in_absence":
        reference = now or datetime.now(tz=None).astimezone()
        absence_subquery = exists().where(
            ScheduleException.employee_id == Employee.id,
            ScheduleException.start_dt <= reference,
            ScheduleException.end_dt >= reference,
        )
        return stmt.where(absence_subquery)

    stmt = stmt.join(EmployeeMetric, EmployeeMetric.employee_id == Employee.id)
    if category == "actual":
        return stmt.where(
            EmployeeMetric.risk_level == "low",
            EmployeeMetric.days_since_update < 60,
        )
    if category == "outdated":
        return stmt.where(EmployeeMetric.days_since_update >= 60)
    if category == "outside_schedule":
        return stmt.where(EmployeeMetric.conflict_rate >= 0.35)
    if category == "overloaded":
        return stmt.where(EmployeeMetric.load_level >= 0.8)
    if category == "hr_calendar_conflict":
        return stmt.where(EmployeeMetric.hr_factor >= 0.5)
    if category == "timezone_conflict":
        return stmt.where(EmployeeMetric.zone_factor >= 0.3)
    if category == "needs_review":
        return stmt.where(
            or_(
                EmployeeMetric.actuality_score < 0.4,
                EmployeeMetric.conflict_rate > 0.5,
                EmployeeMetric.days_since_update > 60,
            )
        )
    if category == "pending_confirmation":
        return stmt.where(EmployeeMetric.risk_level.in_(("medium", "high", "critical")))
    return stmt
